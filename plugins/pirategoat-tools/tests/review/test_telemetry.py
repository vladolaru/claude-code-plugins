"""Tests for review/telemetry.py — JSONL telemetry for PR review pipelines."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent

# Import the module under test
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "telemetry.py"

sys.path.insert(0, str(TESTS_DIR))
from helpers.context_fixtures import COMPLETE_CONTEXT
from helpers.pipeline_process import init_bare_repo
from helpers.review_fixtures import (
    canonical_assignment,
    canonical_findings_ledger,
    canonical_review_document,
)
from review import dependency_refresh
from review import run_paths
from review import synthesis_lifecycle as lifecycle_contract
from review.manifest_sections import aggregate_file_review
from review.reviewer_lifecycle import (
    SCOPE_SUMMARY_SCHEMA,
    ReviewPaths,
    review_paths,
    scope_summary_path,
    started_marker_path,
)


def test_evidence_is_projected_only_when_finalized(mod, tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    telemetry = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
    telemetry.start()
    ledger_path = run_paths.artifact_path(str(out), "review_findings_json")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(canonical_findings_ledger(["high"])))
    telemetry.log_step(step=9, phase="SYNTHESIS", title="Reconcile")
    running = _read_manifest(telemetry)
    assert running["evidence"] is None
    assert running["availability"]["evidence"] is False
    telemetry.finalize(step=12, phase="OUTPUT", title="Complete")
    settled = _read_manifest(telemetry)
    assert settled["availability"]["evidence"] is True
    assert settled["evidence"]["findings"] == [{"id": "f1", "severity": "high", "sources": None, "critic_action": None}]
    assert '"evidence"' not in Path(telemetry.log_path).read_text()


def _load_module():
    spec = importlib.util.spec_from_file_location("review_telemetry", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture
def output_dir(tmp_path):
    """Simulate a PR review output directory."""
    od = tmp_path / "pr-review-org-repo-42"
    od.mkdir()
    return od


@pytest.fixture
def telemetry(mod, output_dir, tmp_path):
    """ReviewTelemetry with a test log_dir."""
    log_dir = tmp_path / "logs"
    return mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))


def _read_events(log_path):
    """Read all JSONL events from a log file."""
    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _read_manifest(telemetry):
    """Read the materialized manifest for a telemetry run."""
    return json.loads(Path(telemetry.manifest_path).read_text())


def _frozen_datetime(mod, *times):
    """Patch mod.datetime so now() returns each of `times` in sequence,
    repeating the last one for any call beyond the given values."""
    calls = [0]
    real_datetime = datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            idx = min(calls[0], len(times) - 1)
            calls[0] += 1
            return times[idx]

    return patch.object(mod, "datetime", FrozenDatetime)


from helpers.review_fixtures import artifact_file as _artifact  # noqa: E402


def _write_dispatch_plan(output_dir, agent_names):
    """Name the agents whose finals the run is entitled to project."""
    _artifact(output_dir, "dispatch_plan").write_text(json.dumps({
        "agents": [
            {"name": name, "status": "DISPATCH"} for name in agent_names
        ],
    }))


def _write_assignment_inputs(output_dir, changed, reviewable, agents):
    """Write the two authoritative path sets assignment measurement reads."""
    (output_dir / "review-context.json").write_text(json.dumps({
        "git": {"changed_files": changed},
    }))
    _artifact(output_dir, "dispatch_plan").write_text(json.dumps({
        "changed_files": reviewable,
        "agents": agents,
    }))


def _write_final_review(output_dir, reviewer, payload):
    path = Path(review_paths(output_dir, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _write_assignment(output_dir, reviewer, payload):
    path = Path(review_paths(output_dir, reviewer).assignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _write_started(output_dir, reviewer):
    path = Path(started_marker_path(output_dir, reviewer))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat())
    return path


def _telemetry_with_finalized_security_review(mod, output_dir, tmp_path):
    """Create telemetry entitled to project one finalized security review."""
    log_dir = tmp_path / "logs"
    review = canonical_review_document("security", ["high", "medium"])
    _write_dispatch_plan(output_dir, ["security-reviewer"])
    _write_final_review(output_dir, "security", review)
    return mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))


class TestAgentNameProjection:
    """Events, the snapshot and the ledger spelled one agent three ways
    (api-contract-reviewer / api-contract / api-contract-review). The
    shared payload carries the registry name only."""

    def test_agent_results_are_keyed_by_agent_name(self, mod, output_dir, tmp_path):
        telemetry = _telemetry_with_finalized_security_review(mod, output_dir, tmp_path)
        agents = telemetry._extract_agent_results()
        assert "security-reviewer" in agents
        assert "security" not in agents

    def test_reconciliation_rosters_are_projected_to_agent_names(self, mod, output_dir, tmp_path):
        ledger = {
            "meta": {
                "reconciliation": {
                    "reviewing_agents": ["security-review", "performance-review"],
                    "dispatched_agents": ["security-reviewer", "performance-reviewer"],
                    "missing_agents": None,
                    "not_applicable_agents": [
                        {"name": "php-tests-review", "skip_reason": "no tests changed"}
                    ],
                }
            }
        }
        projected = mod.ReviewTelemetry._extract_reconciliation(ledger)
        assert projected["reviewing_agents"] == ["security-reviewer", "performance-reviewer"]
        assert projected["dispatched_agents"] == ["security-reviewer", "performance-reviewer"]
        assert projected["missing_agents"] is None
        assert projected["not_applicable_agents"] == [
            {"name": "php-tests-reviewer", "skip_reason": "no tests changed"}
        ]
        assert ledger["meta"]["reconciliation"]["reviewing_agents"] == [
            "security-review", "performance-review"
        ]


# ── start() ─────────────────────────────────────────────────────────


class TestStart:
    """ReviewTelemetry.start() creates log infrastructure."""

    def test_creates_log_with_pipeline_start_event(self, telemetry):
        """start() creates a JSONL log file with a pipeline_start event."""
        path = telemetry.start(pr_number="42")
        assert os.path.isfile(path)
        assert path.endswith(".jsonl")
        assert telemetry.log_path == path
        events = _read_events(path)
        assert len(events) == 1
        assert events[0]["event"] == "pipeline_start"
        assert events[0]["step"] == 0
        # Timestamp is UTC-aware
        ts = datetime.fromisoformat(events[0]["timestamp"])
        assert ts.tzinfo is not None

    def test_pr_number_is_null_outside_pr_mode(self, telemetry):
        path = telemetry.start(pr_number="", identifier="feature-branch", mode="full")
        events = _read_events(path)
        assert events[0]["pipeline"]["pr_number"] is None

    def test_pr_number_accepts_an_int(self, telemetry):
        path = telemetry.start(pr_number=66900)
        events = _read_events(path)
        assert events[0]["pipeline"]["pr_number"] == 66900

    def test_writes_marker_file(self, telemetry, output_dir):
        path = telemetry.start(pr_number="42")
        marker = _artifact(output_dir, "telemetry_log_path")
        assert marker.is_file()
        assert marker.read_text().strip() == path

    def test_start_records_versioned_run_identity(self, telemetry, mod, output_dir):
        path = telemetry.start(
            pr_number="42",
            identifier="42",
            run_id="run-1",
            session_id="session-123",
            plugin_version="1.108.0",
            mode="pr",
            repo_path="/repo",
            git_range="abc..def",
            base_sha="abc",
            head_sha="def",
            total_steps=15,
            bot_mode=False,
        )

        start = _read_events(path)[0]
        assert start["schema"] == mod.EVENT_SCHEMA
        assert "schema_version" not in start
        assert start["run_id"] == "run-1"
        assert start["pipeline"]["pr_number"] == 42
        assert start["pipeline"]["output_dir"] == str(output_dir)
        assert start["pipeline"]["total_steps"] == 15
        assert start["pipeline"]["bot_mode"] is False
        assert start["pipeline"]["session_id"] == "session-123"
        assert start["pipeline"]["plugin_version"] == "1.108.0"
        assert start["pipeline"]["plugin_commit"] == ""
        assert start["pipeline"]["mode"] == "pr"
        assert start["pipeline"]["repo_path"] == "/repo"
        # Passing a non-git-identifiable repo_path (here, a bare path
        # rather than a checkout with an origin remote) records the
        # empty identity rather than raising — `repo_identity` is total.
        assert start["pipeline"]["repo"] == ""
        assert start["pipeline"]["target"] == "42"
        assert start["pipeline"]["git"] == {
            "requested_range": "abc..def",
            "base_sha": "abc",
            "head_sha": "def",
        }

    def test_start_records_origin_repo_identity_and_target(self, telemetry, tmp_path):
        """A started run records the canonical repository and review target,
        and finalize's manifest projects the same identity."""
        repo = init_bare_repo(tmp_path / "checkout", "https://github.com/owner/repository.git")

        path = telemetry.start(repo_path=str(repo), identifier="feature/telemetry")

        pipeline = _read_events(path)[0]["pipeline"]
        assert pipeline["repo"] == "github.com/owner/repository"
        assert pipeline["target"] == "feature/telemetry"

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["run"]["repo"] == "github.com/owner/repository"
        assert manifest["run"]["target"] == "feature/telemetry"

    def test_every_event_inherits_schema_and_run_id(self, telemetry, mod, output_dir, tmp_path):
        log_path = telemetry.start(run_id="run-1")
        later_process = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )
        assert later_process.manifest_path == str(
            Path(log_path).with_suffix(".manifest.json")
        )
        later_process.log_agent_start(agent_name="security-reviewer")

        identities = {
            (event["schema"], event["run_id"])
            for event in _read_events(telemetry.log_path)
        }
        assert identities == {(mod.EVENT_SCHEMA, "run-1")}


# ── path_to_slug() ─────────────────────────────────────────────────


class TestPathToSlug:
    """path_to_slug converts absolute paths to filename-safe slugs.

    The slug is only observable in the log filename, and
    `TestStructuredFilename` pins the exact slug shape (absolute path,
    leading separator stripped) in its regexes — this class covers only
    what those regexes cannot: dots/underscores and separator collapsing.
    """

    @pytest.mark.parametrize(
        "path,expected",
        [
            pytest.param(
                "/my_project/.duplicates/repo",
                "my_project-.duplicates-repo",
                id="preserves-dots-and-underscores",
            ),
            pytest.param(
                "/a///b//c", None, id="collapses-consecutive-separators",
            ),
        ],
    )
    def test_slug_edge_cases(self, mod, path, expected):
        slug = mod.ReviewTelemetry.path_to_slug(path)
        if expected is not None:
            assert slug == expected
        assert "--" not in slug


# ── Structured filename ────────────────────────────────────────────


class TestPrefixCapping:
    """Oversized prefixes are capped so every derived filename fits the
    common 255-byte component limit — an ENAMETOOLONG at allocation would
    be swallowed by the fail-open pipeline into a run with no telemetry."""

    def test_long_branch_name_still_allocates_telemetry(self, mod, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(
            mode="full",
            repo_path="/ci/worktrees/" + "deep/" * 30 + "repo",
            identifier="feature/" + "x" * 300,
        )
        assert os.path.isfile(path)
        assert len(os.path.basename(path).encode("utf-8")) <= 255
        manifest_path = t.manifest_path
        assert os.path.isfile(manifest_path)
        assert len(os.path.basename(manifest_path).encode("utf-8")) <= 255

    def test_capping_is_deterministic_for_repeated_prefixes(
        self, mod, tmp_path
    ):
        out = tmp_path / "output"
        out.mkdir()
        long_prefix = "full-" + "a" * 300
        capped = mod.ReviewTelemetry._cap_prefix(long_prefix)
        assert capped == mod.ReviewTelemetry._cap_prefix(long_prefix)
        assert len(capped.encode("utf-8")) <= mod.ReviewTelemetry._PREFIX_MAX_BYTES

    def test_distinct_long_prefixes_stay_distinct(self, mod):
        base = "full-" + "a" * 300
        assert mod.ReviewTelemetry._cap_prefix(base + "-one") != (
            mod.ReviewTelemetry._cap_prefix(base + "-two")
        )

    def test_capping_is_byte_safe_for_non_ascii_fallback(self, mod):
        """The legacy fallback prefix is not ASCII-sanitized — truncation
        must never split a multibyte character."""
        capped = mod.ReviewTelemetry._cap_prefix("plăți-" + "ă" * 300)
        assert len(capped.encode("utf-8")) <= mod.ReviewTelemetry._PREFIX_MAX_BYTES
        capped.encode("utf-8").decode("utf-8")  # round-trips cleanly


class TestStructuredFilename:
    """Telemetry log filenames use structured prefix--timestamp-nonce format."""

    @pytest.mark.parametrize(
        "mode,repo_path,identifier,pattern",
        [
            pytest.param(
                "pr",
                "/Users/vlad/Work/a8c/woocommerce-payments",
                "42",
                r"^pr-Users-vlad-Work-a8c-woocommerce-payments-42--\d{8}T\d{6}-[0-9a-f]{32}\.jsonl$",
                id="pr-numeric-id",
            ),
            pytest.param(
                "full",
                "/Users/vlad/Work/a8c/ciab-admin",
                "fix/WOOPLUG-123-some-bug",
                r"^full-Users-vlad-Work-a8c-ciab-admin-fix-WOOPLUG-123-some-bug--\d{8}T\d{6}-[0-9a-f]{32}\.jsonl$",
                id="full-slashed-branch-id",
            ),
        ],
    )
    def test_structured_prefix(
        self, mod, tmp_path, mode, repo_path, identifier, pattern
    ):
        """mode-repo_slug-id_slug--timestamp-nonce. One f-string builds the
        prefix, so the mode axis is single-homed; what actually varies is
        the identifier shape (numeric vs. slashed branch), which exercises
        `_UNSAFE_RE` differently."""
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(
            pr_number="42" if mode == "pr" else "",
            mode=mode, repo_path=repo_path, identifier=identifier,
        )
        filename = os.path.basename(path)
        assert re.match(pattern, filename)
        assert filename.endswith(".jsonl")

    def test_same_timestamp_allocates_distinct_logs(self, mod, tmp_path):
        """Concurrent starts never share a JSONL file or durable run identity."""
        log_dir = tmp_path / "logs"
        out1 = tmp_path / "output1"
        out2 = tmp_path / "output2"
        out1.mkdir()
        out2.mkdir()
        fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        with patch.object(mod, "datetime", FrozenDatetime):
            first = mod.ReviewTelemetry(str(out1), log_dir=str(log_dir))
            second = mod.ReviewTelemetry(str(out2), log_dir=str(log_dir))
            first_path = first.start(
                mode="pr", repo_path="/repo", identifier="42", run_id="run-a"
            )
            second_path = second.start(
                mode="pr", repo_path="/repo", identifier="42", run_id="run-b"
            )

        assert first_path != second_path
        assert [(event["event"], event["run_id"]) for event in _read_events(first_path)] == [
            ("pipeline_start", "run-a")
        ]
        assert [(event["event"], event["run_id"]) for event in _read_events(second_path)] == [
            ("pipeline_start", "run-b")
        ]

        later_first = mod.ReviewTelemetry(str(out1), log_dir=str(log_dir))
        later_second = mod.ReviewTelemetry(str(out2), log_dir=str(log_dir))
        later_first.log_agent_start(agent_name="security-reviewer")
        later_second.log_agent_start(agent_name="performance-reviewer")

        assert {event["run_id"] for event in _read_events(first_path)} == {"run-a"}
        assert {event["run_id"] for event in _read_events(second_path)} == {"run-b"}

    def test_missing_identifier_falls_back_to_branch(self, mod, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(mode="full", repo_path="/repo", identifier="")
        filename = os.path.basename(path)
        assert re.match(r"^full-repo-branch--\d{8}T\d{6}-[0-9a-f]{32}\.jsonl$", filename)

    def test_fallback_without_structured_params(self, mod, tmp_path):
        """Legacy callers that don't pass mode/repo_path get output_dir basename."""
        out = tmp_path / "branch-review-some-repo"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(pr_number="42")
        filename = os.path.basename(path)
        assert re.match(r"^branch-review-some-repo--\d{8}T\d{6}-[0-9a-f]{32}\.jsonl$", filename)


# ── log_step() ──────────────────────────────────────────────────────


class TestLogStep:
    """ReviewTelemetry.log_step() appends step events."""

    def test_appends_step_event(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_step(step=3, phase="AWARENESS", title="PR Review State")
        events = _read_events(telemetry.log_path)
        assert len(events) == 2
        assert events[1]["event"] == "step"
        assert events[1]["step"] == 3
        assert events[1]["phase"] == "AWARENESS"
        assert events[1]["title"] == "PR Review State"

    def test_calculates_duration_since_prev(self, telemetry, mod):
        """Duration is calculated from previous event's timestamp."""
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=50)
        with _frozen_datetime(mod, t0, t1):
            telemetry.start(pr_number="42")
            telemetry.log_step(step=1, phase="SETUP", title="Repo Setup")
        events = _read_events(telemetry.log_path)
        assert events[1]["duration_since_prev_ms"] == 50

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        """log_step, finalize, log_agent_start, and log_agent_complete are
        all no-ops when start() was never called — the four writers share
        the same "self.log_path is None" guard."""
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.log_step(step=1, phase="SETUP", title="Repo Setup")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        t.log_agent_start(agent_name="security-reviewer", domain="security")
        t.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        assert t.log_path is None

    def test_repeated_step_events_are_numbered(self, telemetry):
        """Step 11 is logged twice by design (prepare, then publish); the two
        events were indistinguishable in run 6e6a. `attempt` is projected
        one level up into the manifest's `steps`, same numbering."""
        telemetry.start(pr_number="42")
        telemetry.log_step(step=11, phase="OUTPUT", title="Author Report")
        telemetry.log_step(step=11, phase="OUTPUT", title="Author Report")
        telemetry.log_step(step=12, phase="OUTPUT", title="Cleanup")
        events = _read_events(telemetry.log_path)
        attempts = [(e["step"], e["attempt"]) for e in events if e["event"] == "step"]
        assert attempts == [(11, 1), (11, 2), (12, 1)]
        manifest_attempts = [
            (s["step"], s["attempt"]) for s in _read_manifest(telemetry)["steps"]
        ]
        assert manifest_attempts == [(11, 1), (11, 2), (12, 1)]


# ── finalize() ──────────────────────────────────────────────────────


class TestFinalize:
    """ReviewTelemetry.finalize() writes pipeline_end with summary."""

    def test_finalize_writes_pipeline_end_with_summary(self, telemetry, mod):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=50)
        with _frozen_datetime(mod, t0, t1):
            telemetry.start(pr_number="42")
            telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        event = events[-1]
        assert event["event"] == "pipeline_end"
        summary = event["summary"]
        assert isinstance(summary, dict)
        assert summary["total_duration_ms"] == 50
        # Snapshot keys are absent when no source files exist for them.
        snap = event["snapshot"]
        assert "context" not in snap
        assert "dispatch" not in snap
        assert "agent_results" not in snap
        assert "findings" not in snap

    def test_summary_includes_context_fields(self, mod, output_dir, tmp_path):
        """Summary extracts PR size category from review-context.json."""
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        summary = events[-1]["summary"]
        assert summary.get("pr_size_category") == "small"
        assert summary.get("changed_files_count") == 2
        assert summary.get("commit_count") == 3


# ── Unmeasured fields ────────────────────────────────────────


class TestNoFabricatedMeasurements:
    """Zero ≠ unknown: an event may not carry a value nobody measured.

    ``thoughts_length`` used to default to 0 on both writers while no
    caller ever passed it, so every step and pipeline_end event of every
    run reported a measured zero for a measurement that never happened.
    The field was removed at three sites, and each needs its own guard,
    because a guard that only reads freshly written events is satisfied
    by the writer's silence alone: with nothing emitting the key, the
    projection allowlists can be reverted and such a test still passes.
    So the writer guards use fresh events, and the allowlist guard feeds
    a pre-change event shape through the projection — which is the live
    mechanism for old logs, since a manifest is rebuilt from the whole
    JSONL on every refresh. Old logs stay readable either way; the key
    they carry is dropped in projection rather than honored.
    """

    def test_step_and_pipeline_end_events_omit_thoughts_length(
        self, telemetry
    ):
        telemetry.start(pr_number="42")
        telemetry.log_step(step=1, phase="SETUP", title="Repo Setup",
                           bot_mode=True)
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results",
                           bot_mode=True)

        events = _read_events(telemetry.log_path)
        written = [e for e in events if e["event"] in ("step", "pipeline_end")]
        assert [e["event"] for e in written] == ["step", "pipeline_end"]
        for event in written:
            assert event["args"] == {"bot_mode": True}
            assert "thoughts_length" not in json.dumps(event)

    def test_manifest_projection_drops_thoughts_length_from_old_events(
        self, mod, telemetry
    ):
        """The allowlist drops the key, not the writer's silence.

        Written as a pre-change producer would have: the event goes
        straight into the JSONL, so restoring the allowlist entry fails
        this test. A same-run log_step() call could not, since the
        current writer emits no key for the allowlist to select.
        """
        telemetry.start(run_id="run-1")
        old_event = {
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
            "event": "step",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "step": 10,
            "phase": "VALIDATION",
            "title": "Decision Critic",
            "duration_since_prev_ms": 12,
            "args": {"bot_mode": True, "thoughts_length": 321},
            "decisions": {"critic_skipped": True},
        }
        with open(telemetry.log_path, "a") as f:
            f.write(json.dumps(old_event) + "\n")

        # Any refresh reprojects every step event in the log, old included.
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        steps = _read_manifest(telemetry)["steps"]
        assert [step["step"] for step in steps] == [10]
        assert steps[0]["args"] == {"bot_mode": True}
        # Neighbours in the same allowlist must survive the drop.
        assert steps[0]["decisions"] == {"critic_skipped": True}
        assert "thoughts_length" not in json.dumps(steps[0])


# ── Run manifest ───────────────────────────────────────────────


class TestRunManifest:
    """A fail-open sidecar materializes the current run state."""

    def test_host_context_is_projected_from_the_review_context_without_paths(self, telemetry, output_dir):
        (output_dir / "review-context.json").write_text(json.dumps({
            "host_context": {
                "resolved": [{"name": "wordpress", "kind": "runtime-host", "path": "/Users/x/cache/wordpress",
                              "source": "ecosystem-cache", "version": "7.2", "version_freshness": "2026-09-04T00:04:08Z",
                              "notes": {"commit": "abc", "branch": "trunk", "declared_minimum": "7.0"}}],
                "unresolved": [], "banner": None, "diagnostics": {"scan_roots": 3, "self_provided": []},
            },
        }))
        telemetry.start(run_id="run-1")
        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["host_context"] is True
        assert manifest["host_context"]["resolved"][0]["commit"] == "abc"
        assert "branch" not in manifest["host_context"]["resolved"][0]
        assert "/Users/" not in json.dumps(manifest["host_context"])

    def test_start_materializes_running_manifest(self, telemetry, mod):
        log_path = telemetry.start(
            run_id="run-1",
            session_id="session-1",
            plugin_version="1.108.0",
            mode="pr",
            repo_path="/repo",
        )

        assert telemetry.manifest_path == str(
            Path(log_path).with_suffix(".manifest.json")
        )
        manifest = _read_manifest(telemetry)
        assert manifest["schema"] == mod.EVENT_SCHEMA
        assert "schema_version" not in manifest
        assert manifest["status"] == "running"
        assert manifest["run"]["id"] == "run-1"
        assert manifest["run"]["session_id"] == "session-1"
        assert manifest["run"]["plugin_version"] == "1.108.0"
        assert manifest["run"]["plugin_commit"] is None
        assert manifest["run"]["mode"] == "pr"

    def test_manifest_carries_the_build_commit_beside_the_version(self, telemetry):
        """`plugin_version` only moves at release; run 4 stamped 1.119.0
        for the A branch tip and the cohort table could not tell that build
        from the fifty dev-mount runs the next commits would stamp the same."""
        telemetry.start(
            run_id="run-1", session_id="session-1", plugin_version="1.119.0",
            plugin_commit="194489e8", mode="pr", repo_path="/repo",
        )
        start = _read_events(telemetry.log_path)[0]
        assert start["pipeline"]["plugin_commit"] == "194489e8"
        manifest = _read_manifest(telemetry)
        assert manifest["run"]["plugin_commit"] == "194489e8"
        assert manifest["run"]["repo_path"] == "/repo"
        assert manifest["run"]["repo"] is None
        assert manifest["run"]["target"] is None
        assert manifest["run"]["started_at"] is not None
        assert manifest["run"]["ended_at"] is None
        assert manifest["availability"] == {
            "pipeline": True,
            "transcript": False,
            "assignment": False,
            "worktree_hygiene": False,
            "synthesis_agents": False,
            "usage": False,
            "skipped_steps": False,
            "dependency_refresh": False,
            "reviewer_markdown": False,
            "findings_markdown": False,
            "host_context": False,
            "evidence": False,
        }
        assert manifest["assignment"] is None

    def test_manifest_rebuild_projects_missing_repo_and_target_as_none(self, telemetry):
        """A JSONL log whose pipeline_start carries no repo or target still
        rebuilds, projecting both as None rather than failing."""
        telemetry.start(run_id="run-1")
        log_path = Path(telemetry.log_path)
        start = _read_events(log_path)[0]
        start["pipeline"].pop("repo", None)
        start["pipeline"].pop("target", None)
        log_path.write_text(json.dumps(start) + "\n")

        telemetry._materialize_manifest("running")

        manifest = _read_manifest(telemetry)
        assert manifest["run"]["repo"] is None
        assert manifest["run"]["target"] is None

    def test_log_step_refreshes_running_manifest(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        assert manifest["steps"][-1]["step"] == 3
        assert manifest["steps"][-1]["phase"] == "AWARENESS"

    def test_log_step_opens_no_review_artifact(
        self, telemetry, output_dir, monkeypatch
    ):
        """A running manifest is cheap. Only finalize pays for the heavy sections.

        `_build_manifest` ran on every `log_step`, and it re-opened
        `review-findings.json` and every `<reviewer>-review.json` each
        time — roughly 17 opens per file for a 15-step run, for sections
        no consumer reads until the run settles.
        """
        telemetry.start(run_id="run-1")
        _write_final_review(
            output_dir, "code", canonical_review_document("code", ["medium"])
        )
        (output_dir / "review-findings.json").write_text(
            json.dumps(canonical_findings_ledger(["medium"]))
        )

        opened = []
        real_open = open

        def spy(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", spy)
        telemetry.log_step(step=9, phase="SYNTHESIS", title="Review Record")

        assert not [
            path for path in opened
            if path.endswith("/review.json")
            or path.endswith("review-findings.json")
        ]

    def test_a_running_manifest_declares_the_heavy_sections_unavailable(
        self, telemetry, output_dir
    ):
        telemetry.start(run_id="run-1")
        _write_assignment_inputs(
            output_dir,
            ["src/a.py"],
            ["src/a.py"],
            [{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        (output_dir / "review-findings.json").write_text(
            json.dumps(canonical_findings_ledger(["medium"]))
        )

        telemetry.log_step(step=9, phase="SYNTHESIS", title="Review Record")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        for section in ("assignment", "synthesis_agents", "usage"):
            assert manifest[section] is None
            assert manifest["availability"][section] is False
        assert manifest["outcome"]["reconciliation"] is None

    def test_log_step_manifest_allowlists_lifecycle_and_decision_fields(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_step(
            step=10,
            phase="VALIDATION",
            title="Decision Critic",
            bot_mode=True,
            decisions={
                "critic_skipped": True,
                "reason": "SENSITIVE_DECISION_PROSE",
                "prompt": "SENSITIVE_PROMPT",
                "tool_result": {"body": "SENSITIVE_RESULT"},
            },
        )

        step = _read_manifest(telemetry)["steps"][-1]
        assert step["event"] == "step"
        assert step["step"] == 10
        assert step["phase"] == "VALIDATION"
        assert step["title"] == "Decision Critic"
        assert step["args"] == {"bot_mode": True}
        assert step["decisions"] == {"critic_skipped": True}
        serialized = json.dumps(step)
        assert "SENSITIVE_DECISION_PROSE" not in serialized
        assert "SENSITIVE_PROMPT" not in serialized
        assert "SENSITIVE_RESULT" not in serialized
        assert "reason" not in step["decisions"]
        assert "prompt" not in step["decisions"]
        assert "tool_result" not in step["decisions"]

    def test_finalize_materializes_complete_sanitized_outcome_with_reconciliation_verification(
        self, telemetry, output_dir
    ):
        pipeline_result = {
            "status": "degraded",
            "verdict": "COMMENT",
            "critic_verdict": "REVISE",
            "reconciliation_verification": {
                "verified_concern_count": 1, "repository_reads": 2, "status": "verified",
            },
            "review_body": "PIPELINE_RESULT_SECRET",
            "degradation_notes": ["TOOL_RESULT_SECRET"],
        }
        (output_dir / "pipeline-result.json").write_text(json.dumps(pipeline_result))
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        assert manifest["run"]["ended_at"] is not None
        assert manifest["outcome"]["summary"]["total_duration_ms"] is not None
        assert manifest["outcome"]["pipeline_status"] == "degraded"
        assert manifest["outcome"]["verdict"] == "COMMENT"
        assert manifest["outcome"]["critic_verdict"] == "REVISE"
        assert manifest["outcome"]["reconciliation_verification"] == pipeline_result["reconciliation_verification"]
        serialized = json.dumps(manifest)
        assert "PIPELINE_RESULT_SECRET" not in serialized
        assert "TOOL_RESULT_SECRET" not in serialized

    def test_manifest_counts_event_parse_gaps(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")
        with open(telemetry.log_path, "a") as log:
            log.write("not json\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["event_parse_gaps"] == 1

    def test_clean_manifest_omits_event_parse_gaps(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert "event_parse_gaps" not in _read_manifest(telemetry)

    def test_manifest_counts_invalid_utf8_event_parse_gap(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")
        with open(telemetry.log_path, "ab") as log:
            log.write(b"\xff\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["event_parse_gaps"] == 1
        assert manifest["status"] == "complete"
        assert manifest["run"]["id"] == "run-1"
        assert manifest["agents"]["started"][0]["agent"] == (
            "security-reviewer"
        )

    def test_finalize_records_agent_lifecycle_and_incomplete_names(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security"
        )
        telemetry.log_agent_start(
            agent_name="performance-reviewer", domain="performance"
        )
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert [event["agent"] for event in agents["started"]] == [
            "security-reviewer",
            "performance-reviewer",
        ]
        assert [event["agent"] for event in agents["completed"]] == [
            "security-reviewer"
        ]
        assert agents["incomplete"] == ["performance-reviewer"]
        assert "failed" not in agents

    def test_finalize_preserves_unmatched_retry_execution(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["agents"]["incomplete"] == [
            "code-reviewer"
        ]

    def test_start_after_completion_creates_a_retry_execution(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest="b" * 64,
            verdict="comment",
            finding_count=1, severities={"medium": 1},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "comment",
        ]
        assert agents["incomplete"] == []

    def test_overlapping_executions_each_keep_their_completion(
        self, telemetry
    ):
        """Two starts before either completes: both completions match
        outstanding starts — never a false incomplete execution."""
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest="b" * 64,
            verdict="comment",
            finding_count=1, severities={"medium": 1},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "comment",
        ]
        assert agents["incomplete"] == []

    def test_completion_without_start_remains_visible_for_strict_validation(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert agents["started"] == []
        assert [event["agent"] for event in agents["completed"]] == [
            "code-reviewer"
        ]

    def test_agent_events_do_not_refresh_running_manifest(self, telemetry):
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        initial_manifest = manifest_path.read_bytes()

        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )

        assert manifest_path.read_bytes() == initial_manifest
        assert [event["event"] for event in _read_events(telemetry.log_path)] == [
            "pipeline_start",
            "agent_start",
            "agent_complete",
        ]

    @pytest.mark.parametrize(
        "completion_order",
        [("a-reviewer", "b-reviewer"), ("b-reviewer", "a-reviewer")],
        ids=["a-then-b", "b-then-a"],
    )
    def test_finalize_sorts_unmatched_execution_multiset(
        self, telemetry, completion_order
    ):
        telemetry.start(run_id="run-1")
        for agent_name in (
            "a-reviewer",
            "a-reviewer",
            "b-reviewer",
            "b-reviewer",
            "b-reviewer",
        ):
            telemetry.log_agent_start(agent_name=agent_name, domain="code")
        for agent_name in completion_order:
            telemetry.log_agent_complete(
                agent_name=agent_name, review_digest=FINAL_DIGEST,
                verdict="approve",
            )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["agents"]["incomplete"] == [
            "a-reviewer",
            "b-reviewer",
            "b-reviewer",
        ]

    def test_agent_manifest_allowlists_aggregate_severity_fields(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="security-reviewer",
            domain="security",
            model_tier="sonnet",
            scope_files=2,
            scope_lines=40,
            scope_inline_lines=31,
            budget_target=20,
        )
        telemetry.log_agent_complete(
            agent_name="security-reviewer",
            review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=1,
            severities={
                "high": 1,
                "prompt": "SENSITIVE_AGENT_PROMPT",
                "tool_result": {"body": "SENSITIVE_AGENT_RESULT"},
            },
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert agents["started"][0]["scope"] == {
            "files": 2, "lines": 40, "inline_lines": 31,
        }
        assert agents["started"][0]["budget_target"] == 20
        assert agents["completed"][0]["severities"] == {"high": 1}
        serialized = json.dumps(agents)
        assert "SENSITIVE_AGENT_PROMPT" not in serialized
        assert "SENSITIVE_AGENT_RESULT" not in serialized

    def test_agent_manifest_allowlists_only_sanitized_scope_paths(
        self, telemetry
    ):
        telemetry.start(run_id="run-1", repo_path="/repo")
        with open(telemetry.log_path, "a") as log:
            log.write(json.dumps({
                "event": "agent_start",
                "agent": "security-reviewer",
                "scope": {
                    "files": 4,
                    "lines": 80,
                    "paths": [
                        "./src/ok.py",
                        {"nested": "SENSITIVE_NESTED_PATH"},
                        ["SENSITIVE_LIST_PATH"],
                        "../SENSITIVE_TRAVERSAL_PATH",
                        "/Users/alice/SENSITIVE_HOST_PATH",
                    ],
                    "arbitrary": "SENSITIVE_SCOPE_FIELD",
                },
            }) + "\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        started = _read_manifest(telemetry)["agents"]["started"][0]
        assert started["scope"] == {
            "files": 4,
            "lines": 80,
            "paths": ["src/ok.py"],
        }
        serialized = json.dumps(started)
        assert "SENSITIVE_" not in serialized
        assert "arbitrary" not in serialized

    def test_builds_canonical_assigned_excluded_and_unassigned_sets(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=[
                "./src/a.py",
                "src//b.py",
                "docs\\readme.md",
                "vendor/generated.js",
            ],
            reviewable=["src/a.py", "src/b.py", "docs/readme.md"],
            agents=[
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "docs-reviewer", "status": "DISPATCH"},
            ],
        )
        telemetry.start(run_id="run-1", repo_path="/repo")
        telemetry.log_agent_start(
            "security-reviewer",
            scope_paths=[
                "src/b.py",
                "src/a.py",
                "src/a.py",
                "vendor/generated.js",
                "outside/context.py",
            ],
        )
        telemetry.log_agent_start(
            "docs-reviewer", scope_paths=["docs/readme.md"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"] == {
            "changed_files": [
                "docs/readme.md",
                "src/a.py",
                "src/b.py",
                "vendor/generated.js",
            ],
            "reviewable_files": ["docs/readme.md", "src/a.py", "src/b.py"],
            "assigned_files_by_agent": {
                "docs-reviewer": ["docs/readme.md"],
                "security-reviewer": [
                    "src/a.py",
                    "src/b.py",
                    "vendor/generated.js",
                ],
            },
            "assigned_files": ["docs/readme.md", "src/a.py", "src/b.py"],
            "file_exclusions": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"},
            ],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }

    def test_manifest_unassigned_and_recon_unscoped_files_diverge_by_design(
        self, telemetry, output_dir
    ):
        """The one-definition guarantee, in the shape this repo chose:
        these two measurements are NOT unified, and this pins exactly how
        they differ so a future "reconcile the numbers" edit has to argue
        with a test instead of guessing.

        Both answer "which changed files did no agent's scope contain",
        from different evidence over different populations. Read the
        DIVERGENCE NOTE at `manifest_sections.py`'s
        `"unassigned_reviewable_files"` key and
        its reciprocal at `reconciliation_context.py`'s `"unscoped_files"`
        before changing either.
        """
        # Non-ASCII on purpose: the changed set arrives Git-C-quoted (a
        # plain `git diff --name-only`) while every scope producer emits
        # real UTF-8, so both measurements have to decode through the one
        # shared grammar before they can be compared at all. An ASCII-only
        # fixture would pass with either side skipping normalization.
        changed = [
            r'"src/caf\303\251.py"', "src/orphan.py", "vendor/generated.js",
        ]
        _write_assignment_inputs(
            output_dir,
            changed=changed,
            # vendor/generated.js is noise-filtered out of `reviewable`.
            reviewable=[r'"src/caf\303\251.py"', "src/orphan.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1", repo_path="/repo")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/café.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")
        # The runtime sidecar the reviewer actually wrote, unquoted because
        # scope.py runs `-c core.quotepath=false`.
        scope_summary = Path(scope_summary_path(output_dir, "security"))
        scope_summary.parent.mkdir(parents=True, exist_ok=True)
        scope_summary.write_text(
            json.dumps({
                "schema": SCOPE_SUMMARY_SCHEMA,
                "inline_diff_files": ["src/café.py"],
                "review_claimable_files": [],
                "list_only_files": [],
                "routing_files": ["src/café.py"],
            })
        )

        manifest_uncovered = _read_manifest(telemetry)["assignment"][
            "unassigned_reviewable_files"
        ]
        recon_unscoped = aggregate_file_review(
            str(output_dir), changed_files=changed
        )["unscoped_files"]

        # Population: the manifest works over `reviewable`, so the
        # noise-filtered file can never appear there — it is reported under
        # `excluded` instead. The reconciliation context works over the
        # full changed set, so it does.
        assert manifest_uncovered == ["src/orphan.py"]
        assert recon_unscoped == ["src/orphan.py", "vendor/generated.js"]
        assert manifest_uncovered != recon_unscoped
        # And neither reports the covered non-ASCII file as uncovered.
        assert "src/café.py" not in manifest_uncovered
        assert "src/café.py" not in recon_unscoped

    def test_git_c_quoted_unicode_paths_match_real_unicode_scope(
        self, telemetry, output_dir
    ):
        git_quoted = r'"src/\346\270\254\350\251\246.py"'
        unicode_path = "src/測試.py"
        _write_assignment_inputs(
            output_dir,
            changed=[git_quoted],
            reviewable=[git_quoted],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer", scope_paths=[unicode_path]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"]["changed_files"] == [unicode_path]
        assert manifest["assignment"]["reviewable_files"] == [unicode_path]
        assert manifest["assignment"]["assigned_files_by_agent"] == {
            "code-reviewer": [unicode_path],
        }
        assert manifest["assignment"]["assigned_files"] == [unicode_path]
        assert manifest["assignment"]["unassigned_reviewable_files"] == []

    def test_git_quoted_literal_backslash_does_not_collide_with_nested_path(
        self, telemetry, output_dir
    ):
        git_quoted_backslash = r'"src/literal\\name.py"'
        literal_backslash = r"src/literal\name.py"
        nested_path = "src/literal/name.py"
        _write_assignment_inputs(
            output_dir,
            changed=[git_quoted_backslash, nested_path],
            reviewable=[git_quoted_backslash, nested_path],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer",
            scope_paths=[git_quoted_backslash, nested_path],
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assignment = _read_manifest(telemetry)["assignment"]
        assert assignment["changed_files"] == [nested_path, literal_backslash]
        assert assignment["reviewable_files"] == [nested_path, literal_backslash]
        assert assignment["assigned_files_by_agent"]["code-reviewer"] == [
            nested_path,
            literal_backslash,
        ]
        assert assignment["assigned_files"] == [nested_path, literal_backslash]
        assert len(assignment["assigned_files"]) == 2

    def test_quote_delimited_filename_stays_distinct_from_plain_filename(
        self, telemetry, output_dir
    ):
        plain_path = "name.py"
        literal_quoted_path = '"name.py"'
        git_quoted_representation = r'"\"name.py\""'
        _write_assignment_inputs(
            output_dir,
            changed=[plain_path, literal_quoted_path],
            reviewable=[plain_path, literal_quoted_path],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer", scope_paths=[git_quoted_representation]
        )
        event_scope = _read_events(telemetry.log_path)[1]["scope"]["paths"]

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert event_scope == [literal_quoted_path]
        assert manifest["agents"]["started"][0]["scope"]["paths"] == [
            literal_quoted_path,
        ]
        assignment = manifest["assignment"]
        assert assignment["changed_files"] == [literal_quoted_path, plain_path]
        assert assignment["reviewable_files"] == [literal_quoted_path, plain_path]
        assert assignment["assigned_files_by_agent"] == {
            "code-reviewer": [literal_quoted_path],
        }
        assert assignment["assigned_files"] == [literal_quoted_path]
        assert assignment["unassigned_reviewable_files"] == [plain_path]

    def test_raw_quote_delimited_scope_path_without_escape_is_not_git_wrapper(
        self, telemetry
    ):
        literal_quoted_path = '"name.py"'
        telemetry.start(run_id="run-1")

        telemetry.log_agent_start(
            "code-reviewer",
            scope_paths=["name.py", literal_quoted_path],
        )
        event_scope = _read_events(telemetry.log_path)[1]["scope"]["paths"]
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert event_scope == [literal_quoted_path, "name.py"]
        assert _read_manifest(telemetry)["agents"]["started"][0]["scope"][
            "paths"
        ] == event_scope

    def test_malformed_git_quoted_authoritative_path_makes_assignment_unavailable(
        self, telemetry, output_dir
    ):
        """All three decoder-failure shapes (bad escape, invalid UTF-8,
        unterminated quote) land on the same assignment-builder decode
        failure; `invalid-utf8` represents the family."""
        _write_assignment_inputs(
            output_dir,
            changed=[r'"src/\377.py"'],
            reviewable=[r'"src/\377.py"'],
            agents=[],
        )

        telemetry.start(run_id="run-1")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_mixed_invalid_persisted_scope_paths_make_assignment_unavailable(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        with open(telemetry.log_path, "a") as log:
            log.write(json.dumps({
                "event": "agent_start",
                "agent": "code-reviewer",
                "scope": {
                    "paths": [
                        "src/a.py",
                        {"nested": "SENSITIVE_INVALID_SCOPE"},
                    ],
                },
            }) + "\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["agents"]["started"][0]["scope"]["paths"] == [
            "src/a.py",
        ]
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_finally_skipped_agent_assigns_nothing_despite_start_event(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[
                {"name": "security-reviewer", "status": "SKIPPED_OVERRIDE"},
            ],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assignment = _read_manifest(telemetry)["assignment"]
        assert assignment["assigned_files_by_agent"] == {}
        assert assignment["assigned_files"] == []
        assert assignment["unassigned_reviewable_files"] == ["src/a.py"]

    def test_planned_but_never_started_agent_leaves_file_uncovered(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assignment = _read_manifest(telemetry)["assignment"]
        assert assignment["assigned_files_by_agent"] == {}
        assert assignment["assigned_files"] == []
        assert assignment["unassigned_reviewable_files"] == ["src/a.py"]

    def test_retries_merge_scope_paths_for_the_same_agent(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py", "src/b.py"],
            reviewable=["src/a.py", "src/b.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/b.py"]
        )
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py", "src/b.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["assignment"]["assigned_files_by_agent"] == {
            "security-reviewer": ["src/a.py", "src/b.py"],
        }

    @pytest.mark.parametrize(
        "context_payload,plan_payload",
        [
            pytest.param(
                None,
                {"changed_files": ["src/a.py"], "agents": []},
                id="missing-context",
            ),
            pytest.param(
                {"git": {}},
                {"changed_files": ["src/a.py"], "agents": []},
                id="partial-context",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py", None]}},
                {"changed_files": ["src/a.py"], "agents": []},
                id="malformed-context-paths",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py"]}},
                None,
                id="missing-plan",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py"]}},
                {"agents": []},
                id="partial-plan",
            ),
        ],
    )
    def test_assignment_is_explicitly_unavailable_for_incomplete_inputs(
        self, mod, tmp_path, context_payload, plan_payload, capsys
    ):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        if context_payload is not None:
            (output_dir / "review-context.json").write_text(
                context_payload
                if isinstance(context_payload, str)
                else json.dumps(context_payload)
            )
        if plan_payload is not None:
            (_artifact(output_dir, "dispatch_plan")).write_text(
                plan_payload
                if isinstance(plan_payload, str)
                else json.dumps(plan_payload)
            )
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(run_id="run-1")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None
        # Legitimate absence (malformed/partial/missing inputs) is normal
        # operation and must stay silent — only an unexpected builder bug
        # is diagnostic-worthy. See
        # test_unexpected_assignment_builder_exception_is_diagnosed_on_stderr.
        assert capsys.readouterr().err == ""

    def test_unexpected_assignment_builder_exception_is_diagnosed_on_stderr(
        self, mod, telemetry, output_dir, capsys, monkeypatch
    ):
        """A bug inside the assignment builder must be distinguishable
        from the legitimate ``return None`` absence paths above: it still
        yields ``assignment: None`` (fail-open — the run is unaffected) but
        it must be diagnosed on stderr, unlike every silent absence path.
        """
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )

        def _boom(*_args, **kwargs):
            # `repo_path` is passed only by build_assignment_manifest, so
            # this breaks the assignment builder alone and leaves the
            # module's other calls on the same normalizer — which run in
            # the same finalize — working.
            if "repo_path" not in kwargs:
                return normalize_repo_paths(*_args, **kwargs)
            raise RuntimeError("simulated assignment builder bug")

        # The shared repo-path grammar is build_assignment_manifest's sole
        # collaborator; breaking it simulates a real defect in the builder
        # without touching its explicit absence branches (those `return
        # None` directly, never raising). Patched in manifest_sections'
        # namespace, which is where the builder reads it from.
        normalize_repo_paths = mod.manifest_sections.normalize_repo_paths
        monkeypatch.setattr(
            mod.manifest_sections, "normalize_repo_paths", _boom
        )

        # Must not raise: a builder bug must never fail the review run.
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None
        err = capsys.readouterr().err
        assert "assignment manifest build failed" in err
        assert str(output_dir) in err
        assert "simulated assignment builder bug" in err

    def test_valid_empty_path_sets_are_available_zero_assignment(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir, changed=[], reviewable=[], agents=[]
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"] == {
            "changed_files": [],
            "reviewable_files": [],
            "assigned_files_by_agent": {},
            "assigned_files": [],
            "file_exclusions": [],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }

    def test_duplicate_final_agent_names_make_assignment_unavailable(
        self, telemetry, output_dir
    ):
        _write_assignment_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "SKIPPED_OVERRIDE"},
            ],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_assignment_carries_canonical_reviewed_files_per_reviewer(
        self, telemetry, output_dir
    ):
        from review.agent.output import ReviewOutputBuilder, finalize_review

        _write_assignment_inputs(
            output_dir,
            changed=["a.py", "b.py", "c.py"],
            reviewable=["a.py", "b.py", "c.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        _write_assignment(output_dir, "security", canonical_assignment(
            "security", review_claimable_files=["a.py", "b.py", "c.py"],
        ))
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["a.py", "b.py", "c.py"]
        )
        builder = ReviewOutputBuilder.open(str(output_dir), "42", "security")
        builder.claim_files_reviewed("a.py")
        saved = builder.save_draft()
        finalize_review(
            str(output_dir), "security", saved["review_digest"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assignment = _read_manifest(telemetry)["assignment"]
        assert assignment["reviewed_files_by_agent"] == {
            "security-reviewer": {
                "reviewed_file_claim_count": 1,
                "unclaimed_review_file_count": 2,
            },
        }
        assert assignment["review_claimable_file_count_by_agent"] == {
            "security-reviewer": 3
        }

    def test_reviewed_files_rejects_retired_final_review(
        self, mod, output_dir
    ):
        paths = ReviewPaths(
            draft=str(output_dir / "security-review.draft.json"),
            final=str(output_dir / "security-review.json"),
            assignment=str(
                output_dir / "security-assignment.json"
            ),
        )
        Path(paths.final).write_text(json.dumps({
            "schema": 1,
            "reviewer": "security",
            "issues": [],
            "reviewed_file_claims": [],
        }))
        Path(paths.assignment).write_text(json.dumps(canonical_assignment("security")))

        assert mod.manifest_sections._load_final_review(
            str(output_dir), "security-reviewer"
        ) is None

    def test_assignment_omits_unfinalized_draft_counts(
        self, telemetry, output_dir
    ):
        from review.agent.output import ReviewOutputBuilder

        _write_assignment_inputs(
            output_dir,
            changed=["a.py"],
            reviewable=["a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        _write_assignment(output_dir, "security", canonical_assignment(
            "security", review_claimable_files=["a.py"],
        ))
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start("security-reviewer", scope_paths=["a.py"])
        ReviewOutputBuilder.open(
            str(output_dir), "42", "security"
        ).save_draft()
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assignment = _read_manifest(telemetry)["assignment"]
        assert assignment["reviewed_files_by_agent"] == {}
        assert assignment["review_claimable_file_count_by_agent"] == {
            "security-reviewer": 1
        }

    def test_manifest_merges_non_empty_resolved_context_git_identity(
        self, telemetry, output_dir
    ):
        resolved_head = "b" * 40
        telemetry.start(
            run_id="run-1",
            git_range="initial-base..initial-head",
            base_sha="initial-base",
            head_sha="initial-head",
        )
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "resolved-base..resolved-head",
                "merge_base": "",
                "head_sha": resolved_head,
            },
        }))

        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        assert _read_manifest(telemetry)["run"]["git"] == {
            "requested_range": "resolved-base..resolved-head",
            "base_sha": "initial-base",
            "head_sha": resolved_head,
            "base_fetch": None,
            "scope_check": None,
        }

    def test_manifest_projects_the_base_fetch_and_scope_check(self, mod, tmp_path):
        """A dropped range-truth projection would hide a mismatched review range."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        telemetry = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        telemetry.start(
            pr_number="42", mode="pr", repo_path=str(tmp_path),
            git_range="main..feature", base_sha="a" * 40, head_sha="b" * 40,
        )
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "main..feature",
                "base_fetch": {"ref": "origin/main", "status": "fetched", "sha": "a" * 40, "shallow": False},
                "scope_check": {
                    "status": "mismatch", "github_changed_files": 8, "local_changed_files": 91,
                    "head_matches": True, "base_matches": False,
                    "extra_local_files": ["src/a.php", "src/b.php"], "missing_local_files": [],
                },
            },
        }))
        telemetry.finalize(step=12, phase="done", title="Done")

        git = json.loads(Path(telemetry.manifest_path).read_text())["run"]["git"]
        assert git["base_fetch"] == {"status": "fetched", "sha": "a" * 40, "shallow": False}
        assert git["scope_check"] == {
            "status": "mismatch", "github_changed_files": 8, "local_changed_files": 91,
            "head_matches": True, "base_matches": False,
            "extra_local_file_count": 2, "missing_local_file_count": 0,
        }
        assert "origin/main" not in json.dumps(git)
        assert "src/a.php" not in json.dumps(git)

    def test_range_fact_projection_rejects_non_string_status_without_aborting(self, mod):
        """A malformed nested status must not abort manifest finalization."""
        status = {}
        fetch = mod._project_base_fetch({"status": status, "sha": "a" * 40, "shallow": False})
        scope = mod._project_scope_check({"status": status, "github_changed_files": 1})

        assert fetch["status"] is None
        assert scope["status"] is None

    def test_manifest_refresh_keeps_resolved_shas_over_symbolic_context_refs(
        self, telemetry, output_dir
    ):
        """An explicit symbolic range stores "main" as context merge_base;
        the refresh must not replace the resolved durable identity with a
        movable ref."""
        resolved_base = "a" * 40
        resolved_head = "b" * 40
        telemetry.start(
            run_id="run-1",
            git_range="main..HEAD",
            base_sha=resolved_base,
            head_sha=resolved_head,
        )
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "main..HEAD",
                "merge_base": "main",
                "head_sha": "HEAD",
            },
        }))

        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        assert _read_manifest(telemetry)["run"]["git"] == {
            "requested_range": "main..HEAD",
            "base_sha": resolved_base,
            "head_sha": resolved_head,
            "base_fetch": None,
            "scope_check": None,
        }

    def test_manifest_compares_planner_and_orchestrator_dispatches(
        self, telemetry, output_dir
    ):
        initial = {
            "agent_signals": [
                "security-reviewer: STATUS=DISPATCH (keywords matched (files: auth))",
                "a11y-reviewer: STATUS=SKIPPED_TRIAGE (no UI signal)",
                "code-reviewer: STATUS=DISPATCH",
            ],
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "DISPATCH",
                    "reason": "keywords matched (files: auth)",
                    "signal": "keyword",
                },
                {
                    "name": "a11y-reviewer",
                    "domain": "a11y",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "no UI signal",
                    "signal": "evidence_gate",
                },
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always dispatch (domain has files)",
                    "signal": "always",
                },
            ]
        }
        final = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "keywords matched (files: auth)",
                    "signal": "keyword",
                    "override_reason": "change does not touch an auth boundary",
                },
                {
                    "name": "a11y-reviewer",
                    "domain": "a11y",
                    "status": "DISPATCH_OVERRIDE",
                    "reason": "no UI signal",
                    "signal": "evidence_gate",
                    "override_reason": "rendered markup coverage was missed",
                },
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always dispatch (domain has files)",
                    "signal": "always",
                },
            ]
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps(initial))
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is True
        assert dispatch["adjustment_counts"] == {
            "added": 1,
            "removed": 1,
            "unchanged": 1,
        }
        assert dispatch["planner_candidate_count"] == 2
        assert dispatch["final_dispatch_count"] == 2

        removed = dispatch["agents"]["security-reviewer"]
        assert removed == {
            "domain": "security",
            "initial_status": "DISPATCH",
            "initial_reason": "keywords matched (files: auth)",
            "final_status": "SKIPPED_OVERRIDE",
            "final_reason": "keywords matched (files: auth)",
            "initial_signal": "keyword",
            "final_signal": "override",
            "planner_signals": [
                "security-reviewer: STATUS=DISPATCH (keywords matched (files: auth))"
            ],
            "configured_planner_checks": [],
            "model_tier": "sonnet",
            "declared_model": None,
            "adjustment_reason": "change does not touch an auth boundary",
            "change": "removed",
        }
        added = dispatch["agents"]["a11y-reviewer"]
        assert added["initial_status"] == "SKIPPED_TRIAGE"
        assert added["final_status"] == "DISPATCH_OVERRIDE"
        assert added["initial_signal"] == "evidence_gate"
        assert added["final_signal"] == "override"
        assert added["adjustment_reason"] == "rendered markup coverage was missed"
        assert added["change"] == "added"
        assert added["configured_planner_checks"] == [
            "has_markup_changes",
            "has_style_files",
            "has_template_files",
        ]
        assert added["model_tier"] == "opus"
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"
        assert dispatch["agents"]["code-reviewer"]["initial_signal"] == "always"
        assert dispatch["agents"]["code-reviewer"]["final_signal"] == "always"

    def test_repo_reviewer_model_override_reaches_dispatch_telemetry(
        self, telemetry, output_dir
    ):
        """Adapter entries carry their explicit override under "model" (the
        dispatch contract step 6 honors) and have no registry fallback —
        without reading it, their requested tier is omitted."""
        entry = {
            "name": "repo-renewals-reviewer",
            "domain": None,
            "status": "DISPATCH",
            "reason": "repo-declared reviewer applies",
            "adapter": "repo-reviewer-adapter",
            "model": "opus",
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps({"agents": [entry]})
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(
            json.dumps({"agents": [entry]})
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["agents"]["repo-renewals-reviewer"]["model_tier"] == "opus"

    def test_repo_reviewer_declared_model_reaches_dispatch_telemetry(
        self, telemetry, output_dir
    ):
        entry = {
            "name": "repo-renewals-reviewer",
            "domain": None,
            "status": "DISPATCH",
            "reason": "repo-declared reviewer applies",
            "adapter": "repo-reviewer-adapter",
            "model": "inherit",
            "declared_model": "opus",
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps({"agents": [entry]})
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(
            json.dumps({"agents": [entry]})
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        decision = _read_manifest(telemetry)["dispatch"]["agents"][
            "repo-renewals-reviewer"
        ]
        assert decision["model_tier"] == "inherit"
        assert decision["declared_model"] == "opus"

    # manifest_sections.py:203-207 calls the same inspect_dispatch_plan() for
    # both plans and both land on the one validator at dispatch_status.py:50-53,
    # so the plan_name axis doubled nodes without reaching new code. One param
    # per invalid shape the validator can distinguish is what remains.
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            "UNKNOWN",
        ],
    )
    def test_manifest_rejects_incomplete_dispatch_statuses(
        self, telemetry, output_dir, invalid_status
    ):
        initial_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        final_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        if invalid_status == "__missing__":
            initial_agent.pop("status")
        else:
            initial_agent["status"] = invalid_status
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps({"agents": [initial_agent]})
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(
            json.dumps({"agents": [final_agent]})
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["comparison_available"] is False
        assert dispatch["planner_baseline_available"] is False
        assert "planner_baseline_unavailable" in dispatch["invalid_reason_codes"]
        assert dispatch["final_plan_available"] is True
        assert dispatch["agents"]["code-reviewer"]["initial_status"] == "DISPATCH"
        assert dispatch["agents"]["code-reviewer"]["final_status"] == "DISPATCH"
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"

    @pytest.mark.parametrize(
        "status,dispatched",
        [
            ("DISPATCH_OVERRIDE", True),
            ("SKIPPED_TRIAGE", False),
        ],
    )
    def test_manifest_accepts_supported_dispatch_status_vocabulary(
        self, telemetry, output_dir, status, dispatched
    ):
        plan = {"agents": [{"name": "code-reviewer", "status": status}]}
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps(plan))
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["comparison_available"] is True
        assert dispatch["planner_candidate_count"] == int(dispatched)
        assert dispatch["final_dispatch_count"] == int(dispatched)
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"

    def test_manifest_agent_set_mismatch_disables_only_comparison(
        self, telemetry, output_dir,
    ):
        """A symmetric set inequality; one direction (an agent added
        between the planner baseline and the final plan) proves it."""
        initial_names = ["code-reviewer"]
        final_names = ["code-reviewer", "security-reviewer"]
        planner_count, final_count = 1, 2

        def plan(names):
            return {
                "agents": [
                    {"name": name, "status": "DISPATCH"}
                    for name in names
                ]
            }

        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps(plan(initial_names))
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(
            json.dumps(plan(final_names))
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch == {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": False,
            "planner_candidate_count": planner_count,
            "final_dispatch_count": final_count,
            "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
            "invalid_reason_codes": ["dispatch_agent_set_mismatch"],
            "agents": {},
            "plan_projections": {
                "planner_baseline": {
                    name: "DISPATCH" for name in initial_names
                },
                "final_plan": {
                    name: "DISPATCH" for name in final_names
                },
            },
        }

    def test_manifest_agent_set_mismatch_projects_sorted_statuses_without_plan_prose(
        self, telemetry, output_dir
    ):
        initial = {
            "agents": [
                {
                    "name": "z-reviewer",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "SENSITIVE_INITIAL_REASON",
                    "raw_diff": "SENSITIVE_INITIAL_SOURCE",
                },
                {"name": "a-reviewer", "status": "DISPATCH"},
            ]
        }
        final = {
            "agents": [
                {
                    "name": "m-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "override_reason": "SENSITIVE_FINAL_REASON",
                    "private_notes": ["SENSITIVE_FINAL_FINDING"],
                },
                {"name": "a-reviewer", "status": "DISPATCH_OVERRIDE"},
            ]
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps(initial)
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 1
        assert dispatch["plan_projections"] == {
            "planner_baseline": {
                "a-reviewer": "DISPATCH",
                "z-reviewer": "SKIPPED_TRIAGE",
            },
            "final_plan": {
                "a-reviewer": "DISPATCH_OVERRIDE",
                "m-reviewer": "SKIPPED_OVERRIDE",
            },
        }
        assert list(dispatch["plan_projections"]["planner_baseline"]) == [
            "a-reviewer",
            "z-reviewer",
        ]
        assert list(dispatch["plan_projections"]["final_plan"]) == [
            "a-reviewer",
            "m-reviewer",
        ]
        serialized = json.dumps(dispatch)
        assert not any(
            sentinel in serialized
            for sentinel in (
                "SENSITIVE_INITIAL_REASON",
                "SENSITIVE_INITIAL_SOURCE",
                "SENSITIVE_FINAL_REASON",
                "SENSITIVE_FINAL_FINDING",
            )
        )

    def test_manifest_agent_set_mismatch_allows_one_empty_identity_set(
        self, telemetry, output_dir
    ):
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(
            json.dumps({"agents": []})
        )
        (_artifact(output_dir, "dispatch_plan")).write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "security-reviewer",
                            "status": "SKIPPED_TRIAGE",
                        }
                    ]
                }
            )
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["planner_candidate_count"] == 0
        assert dispatch["final_dispatch_count"] == 0
        assert dispatch["plan_projections"] == {
            "planner_baseline": {},
            "final_plan": {"security-reviewer": "SKIPPED_TRIAGE"},
        }

    @pytest.mark.parametrize(
        "mode",
        ["comparable", "legacy-final", "duplicate"],
    )
    def test_manifest_omits_plan_projections_outside_agent_set_mismatch(
        self, telemetry, output_dir, mode
    ):
        plan = {
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}]
        }
        if mode in {"comparable", "duplicate"}:
            initial = plan
            if mode == "duplicate":
                initial = {"agents": plan["agents"] * 2}
            (_artifact(output_dir, "dispatch_plan_initial")).write_text(
                json.dumps(initial)
            )
        if mode in {"comparable", "legacy-final", "duplicate"}:
            (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")

        assert "plan_projections" not in _read_manifest(telemetry)["dispatch"]

    def test_manifest_legacy_plan_falls_back_to_unchanged_baseline(
        self, telemetry, mod, output_dir, tmp_path
    ):
        """An absent initial dispatch plan (no baseline was ever recorded)
        and a malformed one (baseline present but unreadable) both read as
        `planner_baseline_available: False` and project every final-plan
        agent as `unchanged`."""
        final = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "DISPATCH_OVERRIDE",
                    "reason": "legacy plan",
                    "override_reason": "legacy adjustment without a baseline",
                }
            ]
        }
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is False
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is False
        decision = dispatch["agents"]["security-reviewer"]
        assert decision["initial_status"] == "DISPATCH_OVERRIDE"
        assert decision["final_status"] == "DISPATCH_OVERRIDE"
        assert decision["change"] == "unchanged"
        assert dispatch["adjustment_counts"] == {
            "added": 0,
            "removed": 0,
            "unchanged": 1,
        }
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 1

        malformed_out = tmp_path / "malformed-output"
        malformed_out.mkdir()
        t2 = mod.ReviewTelemetry(
            str(malformed_out), log_dir=str(tmp_path / "logs2")
        )
        (_artifact(malformed_out, "dispatch_plan_initial")).write_text("NOT JSON")
        (_artifact(malformed_out, "dispatch_plan")).write_text(json.dumps({
            "agents": [
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always",
                }
            ]
        }))

        t2.start(run_id="run-2")

        dispatch2 = _read_manifest(t2)["dispatch"]
        assert dispatch2["planner_baseline_available"] is False
        assert dispatch2["final_plan_available"] is True
        assert dispatch2["comparison_available"] is False
        assert dispatch2["agents"]["code-reviewer"]["change"] == "unchanged"
        assert dispatch2["adjustment_counts"] == {
            "added": 0,
            "removed": 0,
            "unchanged": 1,
        }

    def test_manifest_dispatch_is_fail_open_for_malformed_partial_plans(
        self, telemetry, output_dir
    ):
        (_artifact(output_dir, "dispatch_plan_initial")).write_text("NOT JSON")
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps({
            "agents": [
                None,
                "not-an-agent",
                {"status": "DISPATCH"},
                {
                    "name": "code-reviewer",
                    "status": "DISPATCH",
                    "reason": "always",
                },
            ]
        }))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        dispatch = manifest["dispatch"]
        assert dispatch["planner_baseline_available"] is False
        assert dispatch["final_plan_available"] is False
        assert dispatch["comparison_available"] is False
        assert dispatch["agents"] == {}
        assert "planner_baseline_unavailable" in dispatch["invalid_reason_codes"]
        assert "final_plan_unavailable" in dispatch["invalid_reason_codes"]

    def test_manifest_dispatch_allowlist_omits_arbitrary_plan_payloads(
        self, telemetry, output_dir
    ):
        plan = {
            "prompt": "SENSITIVE_PLAN_PROMPT",
            "tool_result": {"body": "SENSITIVE_PLAN_RESULT"},
            "agents": [
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always",
                    "focus": "SENSITIVE_FOCUS_PROSE",
                    "raw_diff": "SENSITIVE_SOURCE",
                    "private_notes": ["SENSITIVE_FINDING"],
                }
            ],
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps(plan))
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        serialized = Path(telemetry.manifest_path).read_text()
        assert not any(sentinel in serialized for sentinel in (
            "SENSITIVE_PLAN_PROMPT",
            "SENSITIVE_PLAN_RESULT",
            "SENSITIVE_FOCUS_PROSE",
            "SENSITIVE_SOURCE",
            "SENSITIVE_FINDING",
        ))

    def test_manifest_continues_when_final_dispatch_plan_is_malformed(
        self, telemetry, output_dir
    ):
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}]
        }))
        (_artifact(output_dir, "dispatch_plan")).write_text("NOT JSON")

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        dispatch = manifest["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is False
        assert dispatch["comparison_available"] is False
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 0
        assert dispatch["agents"] == {}

    def test_manifest_distinguishes_observed_zero_from_unavailable_zero(
        self, telemetry, output_dir
    ):
        telemetry.start(run_id="run-1")

        unavailable = _read_manifest(telemetry)["dispatch"]
        assert unavailable["planner_candidate_count"] == 0
        assert unavailable["final_dispatch_count"] == 0
        assert unavailable["planner_baseline_available"] is False
        assert unavailable["final_plan_available"] is False
        assert unavailable["comparison_available"] is False

        empty_plan = {"agents": []}
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps(empty_plan))
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(empty_plan))
        telemetry.log_step(step=5, phase="EXECUTION", title="Dispatch Plan")

        observed = _read_manifest(telemetry)["dispatch"]
        assert observed["planner_candidate_count"] == 0
        assert observed["final_dispatch_count"] == 0
        assert observed["planner_baseline_available"] is True
        assert observed["final_plan_available"] is True
        assert observed["comparison_available"] is True

    def test_manifest_duplicate_agents_invalidate_comparison_but_keep_raw_counts(
        self, telemetry, output_dir
    ):
        initial = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": "DISPATCH",
                    "reason": "signal",
                    "prompt": "SENSITIVE_DUPLICATE_PROMPT",
                },
                {
                    "name": "security-reviewer",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "conflicting duplicate",
                    "tool_result": "SENSITIVE_DUPLICATE_RESULT",
                },
            ]
        }
        final = {
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH_OVERRIDE"},
            ]
        }
        (_artifact(output_dir, "dispatch_plan_initial")).write_text(json.dumps(initial))
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is False
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 2
        assert dispatch["duplicate_agent_names"] == {
            "planner_baseline": ["security-reviewer"],
            "final_plan": ["security-reviewer"],
        }
        assert dispatch["agents"] == {}
        serialized = json.dumps(dispatch)
        assert "SENSITIVE_DUPLICATE_PROMPT" not in serialized
        assert "SENSITIVE_DUPLICATE_RESULT" not in serialized

    def test_manifest_omits_pr_prompt_finding_and_tool_result_prose(
        self, telemetry, output_dir
    ):
        sentinels = {
            "PR_TITLE_SECRET",
            "PR_AUTHOR_SECRET",
            "PR_BODY_SECRET",
            "RAW_PROMPT_SECRET",
            "SOURCE_SECRET",
            "FINDING_SECRET",
            "TOOL_RESULT_SECRET",
        }
        (output_dir / "review-context.json").write_text(json.dumps({
            "pr": {
                "title": "PR_TITLE_SECRET",
                "author": "PR_AUTHOR_SECRET",
                "body": "PR_BODY_SECRET",
            },
            "prompt": "RAW_PROMPT_SECRET",
            "source": "SOURCE_SECRET",
            "git": {
                "git_range": "base..head",
                "merge_base": "base",
                "head_sha": "head",
            },
        }))
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "comment",
            "summary": "FINDING_SECRET",
            "findings": [{
                "severity": "medium",
                "description": "FINDING_SECRET",
            }],
        }))
        (output_dir / "pipeline-result.json").write_text(json.dumps({
            "status": "complete",
            "verdict": "COMMENT",
            "critic_verdict": "STAND",
            "tool_result": "TOOL_RESULT_SECRET",
        }))
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        serialized = Path(telemetry.manifest_path).read_text()
        assert not any(sentinel in serialized for sentinel in sentinels)

    def test_manifest_replace_failure_preserves_events_and_cleans_temp(
        self, telemetry, mod
    ):
        """`_materialize_manifest`'s fail-open `except` around `os.replace`
        is one code path reached from all three entry points; driving
        start, log_step, and finalize through one patched `os.replace`
        proves the event log survives at each of them and no temp file is
        left behind."""
        with patch.object(
            mod.os, "replace", side_effect=OSError("nope")
        ) as replace:
            telemetry.start(run_id="run-1")
            existing = set(Path(telemetry.log_dir).iterdir())
            telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")
            assert set(Path(telemetry.log_dir).iterdir()) == existing
            telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert replace.call_count == 3
        events = _read_events(telemetry.log_path)
        assert events[0]["event"] == "pipeline_start"
        assert events[1]["step"] == 3
        assert events[-1]["event"] == "pipeline_end"
        assert set(Path(telemetry.log_dir).iterdir()) == existing


# ── Snapshot extraction ─────────────────────────────────────────────


class TestSnapshot:
    """Snapshot extraction — only present in finalize() / pipeline_end."""

    def test_context_changed_files_are_normalized_and_deduplicated(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "changed_files": [
                    "./src/a.py",
                    "src//a.py",
                    "tests\\test_a.py",
                ],
            },
        }))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        context = _read_events(telemetry.log_path)[-1]["snapshot"]["context"]
        assert context["changed_files"] == ["src/a.py", "tests/test_a.py"]
        assert context["changed_files_count"] == 2

    def test_invalid_dispatch_plan_omits_snapshot_and_summary(
        self, mod, output_dir, tmp_path
    ):
        plan = {
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCHED"},
            ],
        }
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        event = _read_events(telemetry.log_path)[-1]
        assert "dispatch" not in event["snapshot"]
        assert "agents_total" not in event["summary"]
        assert "agents_dispatched" not in event["summary"]
        assert "agents_skipped" not in event["summary"]

    def test_extracts_agent_results(self, mod, output_dir, tmp_path):
        t = _telemetry_with_finalized_security_review(mod, output_dir, tmp_path)
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        agents = events[-1]["snapshot"]["agent_results"]
        assert "security-reviewer" in agents
        assert agents["security-reviewer"]["verdict"] == "request_changes"
        assert agents["security-reviewer"]["finding_count"] == 2
        assert agents["security-reviewer"]["severities"]["high"] == 1

    def test_retired_agent_review_extracts_only_malformed_evidence(
        self, mod, output_dir, tmp_path
    ):
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        _write_final_review(output_dir, "security", {
            "schema": 1,
            "reviewer": "security",
            "issues": [],
            "verdict": "approve",
        })
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        assert telemetry._extract_agent_results()["security-reviewer"] == {
            "error": "malformed"
        }

    def test_excludes_review_findings_from_agent_results(self, mod, output_dir, tmp_path):
        """review-findings.json is reconciled output, not an agent result.

        Asserted on a run that HAS an agent result, so the projection is
        populated and the boundary is a real one: the ledger belongs to
        `findings` and the dispatched reviewer to `agent_results`, and
        neither section may borrow from the other.
        """
        log_dir = tmp_path / "logs"
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        _write_final_review(
            output_dir, "security", canonical_review_document("security", ["medium"])
        )
        (output_dir / "review-findings.json").write_text(json.dumps(
            canonical_findings_ledger(["medium"])
        ))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        snap = events[-1]["snapshot"]
        assert set(snap["agent_results"]) == {"security-reviewer"}
        assert snap["findings"]["final_finding_count"] == 1

    def test_extracts_findings(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        findings = canonical_findings_ledger(["high", "medium", "low"])
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        f = events[-1]["snapshot"]["findings"]
        assert f["verdict"] == "request_changes"
        assert f["final_finding_count"] == 3
        assert f["severities"]["high"] == 1

    def test_severities_carry_every_severity_including_the_zeros(
        self, mod, output_dir, tmp_path
    ):
        """A zero is a measurement, and every projection publishes all five.

        The recounts these replaced built their dict from the findings
        actually present, so a manifest could not tell "no critical
        findings" from "critical was never counted". Both severity maps —
        the per-agent one and the ledger's, which `outcome.summary`
        republishes as `final_severities` — now carry the whole
        vocabulary.
        """
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        _write_final_review(
            output_dir, "security", canonical_review_document("security", ["high"])
        )
        (output_dir / "review-findings.json").write_text(json.dumps(
            canonical_findings_ledger(["high"])
        ))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        expected = {
            "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0,
        }
        snapshot = _read_events(t.log_path)[-1]["snapshot"]
        assert snapshot["agent_results"]["security-reviewer"]["severities"] == expected
        assert snapshot["findings"]["severities"] == expected
        assert _read_manifest(t)["outcome"]["summary"][
            "final_severities"
        ] == expected

    def test_findings_measurement_reaches_summary_and_manifest(
        self, mod, output_dir, tmp_path
    ):
        findings = canonical_findings_ledger(["critical"])
        findings["findings"][0]["channel"] = "advisory"
        findings["verdict"] = "approve"
        findings["summary"].update({
            "suppressed_advisory_finding_count": 1,
            "verdict_without_advisory": "block",
        })
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        event = _read_events(t.log_path)[-1]
        snapshot = event["snapshot"]["findings"]
        assert snapshot["suppressed_advisory_finding_count"] == 1
        assert snapshot["verdict_without_advisory"] == "block"
        assert event["summary"]["final_suppressed_advisory_finding_count"] == 1
        assert event["summary"]["final_verdict_without_advisory"] == "block"
        manifest_summary = _read_manifest(t)["outcome"]["summary"]
        assert manifest_summary["final_suppressed_advisory_finding_count"] == 1
        assert manifest_summary["final_verdict_without_advisory"] == "block"

    def test_handles_malformed_json(self, mod, output_dir, tmp_path):
        """Malformed files are skipped gracefully."""
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text("NOT JSON")
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        assert "context" not in events[-1]["snapshot"]

    def test_list_shaped_findings_file_extracts_empty_and_finalize_completes(
        self, mod, output_dir, tmp_path
    ):
        """A non-object review-findings.json (a list) must not crash finalize.

        Both extractors must route through critic_adjustments.read_findings_file
        so a non-object payload degrades to the extractor's own empty/default
        return instead of an uncaught AttributeError escaping finalize() and
        silently losing the whole pipeline_end event (manifest finalize,
        summary, durations) into pipeline.py's blanket except.
        """
        (output_dir / "review-findings.json").write_text(json.dumps([1, 2, 3]))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        events = _read_events(t.log_path)
        assert events[-1]["event"] == "pipeline_end"
        assert "findings" not in events[-1]["snapshot"]
        assert t._extract_findings() is None

    def test_string_shaped_agent_review_file_extracts_malformed_and_finalize_completes(
        self, mod, output_dir, tmp_path
    ):
        """A non-object <agent>-review.json (a string) must not crash finalize.

        See test_list_shaped_findings_file_extracts_empty_and_finalize_completes
        for the shared rationale — this is the sibling extractor.
        """
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        _write_final_review(output_dir, "security", "oops")
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        events = _read_events(t.log_path)
        assert events[-1]["event"] == "pipeline_end"
        assert events[-1]["snapshot"]["agent_results"]["security-reviewer"] == {
            "error": "malformed"
        }
        assert t._extract_agent_results()["security-reviewer"] == {"error": "malformed"}


# ── Re-reviews ──────────────────────────────────────────────────────


# ── log_agent_start() ────────────────────────────────────────────


class TestLogAgentStart:
    """ReviewTelemetry.log_agent_start() appends agent lifecycle events."""

    def test_agent_start_event_full_shape(self, telemetry, mod):
        telemetry.start(run_id="run-1")

        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security",
            model_tier="sonnet", scope_files=3, scope_lines=150,
            scope_inline_lines=120,
            budget_target=35,
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event == {
            "event": "agent_start",
            "timestamp": event["timestamp"],
            "agent": "security-reviewer",
            "domain": "security",
            "model_tier": "sonnet",
            # Two different sizes: the diffstat total that sized the budget,
            # and the hunk lines the briefing actually carried.
            "scope": {"files": 3, "lines": 150, "inline_lines": 120},
            "budget_target": 35,
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
        }

    def test_agent_start_omits_budget_when_none(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security",
        )
        event = _read_events(telemetry.log_path)[-1]
        assert "budget_target" not in event

    def test_agent_start_omits_inline_lines_when_unmeasured(self, telemetry):
        """An absent count must stay absent: a caller that could not measure
        the briefing size has not measured a zero-line briefing."""
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="security-reviewer", scope_files=3, scope_lines=150,
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event["scope"] == {"files": 3, "lines": 150}

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")
        started = _read_manifest(telemetry)["agents"]["started"][0]
        assert "inline_lines" not in started["scope"]

    def test_null_domain_is_canonicalized_to_empty_string(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="tests-mutation-reviewer", domain=None
        )
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")

        events = _read_events(telemetry.log_path)
        start = next(event for event in events if event["event"] == "agent_start")
        assert start["domain"] == ""
        assert _read_manifest(telemetry)["agents"]["started"][0]["domain"] == ""

    def test_scope_paths_are_normalized_deduplicated_and_safely_relativized(
        self, mod, tmp_path
    ):
        repo = tmp_path / "repo"
        output = tmp_path / "output"
        repo.mkdir()
        output.mkdir()
        telemetry = mod.ReviewTelemetry(
            str(output), log_dir=str(tmp_path / "logs")
        )
        telemetry.start(run_id="run-1", repo_path=str(repo))

        telemetry.log_agent_start(
            agent_name="security-reviewer",
            scope_files=7,
            scope_lines=20,
            scope_paths=[
                "./src/a.py",
                "src//a.py",
                "tests\\test_a.py",
                str(repo / "src" / "absolute.py"),
                "src/../SENSITIVE_TRAVERSAL.py",
                str(tmp_path / "SENSITIVE_OUTSIDE.py"),
                "C:SENSITIVE_DRIVE_RELATIVE.py",
                r"C:\SENSITIVE_DRIVE_ABSOLUTE.py",
                {"nested": "SENSITIVE_DICT"},
                ["SENSITIVE_LIST"],
                42,
            ],
        )

        start_event = _read_events(telemetry.log_path)[1]
        assert start_event["scope"] == {
            "files": 7,
            "lines": 20,
            "paths": [
                "src/a.py",
                "src/absolute.py",
                "tests/test_a.py",
            ],
        }
        assert "SENSITIVE_" not in json.dumps(start_event)

    def test_scope_paths_reject_unicode_control_and_format_characters(
        self, telemetry
    ):
        """One character-class regex in `git_paths` rejects both control
        and format Unicode categories alike; `unicode-control` represents
        the family."""
        telemetry.start(run_id="run-1")

        telemetry.log_agent_start(
            agent_name="security-reviewer",
            scope_paths=[
                "src/control\x7fname.py",
                "src/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py",
            ],
        )

        start_event = _read_events(telemetry.log_path)[1]
        assert start_event["scope"]["paths"] == ["src/café.py"]


# ── reviewer publication events ──────────────────────────────────


FINAL_DIGEST = "a" * 64


class TestLogAgentReviewDraftSaved:
    def test_records_digest_bound_draft_evidence(self, telemetry, mod):
        telemetry.start(run_id="run-1")

        telemetry.log_agent_review_draft_saved(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event == {
            "event": "agent_review_draft_saved",
            "timestamp": event["timestamp"],
            "agent": "security-reviewer",
            "review_digest": FINAL_DIGEST,
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
        }


class TestLogAgentComplete:
    """ReviewTelemetry.log_agent_complete() appends completion events."""

    def test_agent_complete_event_full_shape(self, telemetry, output_dir, mod):
        """Full-event equality also proves the retired `issue_count` noun
        stays gone — renaming only the review artifacts would otherwise
        leave lifecycle telemetry still teaching it."""
        telemetry.start(run_id="run-1")
        _write_started(output_dir, "security")

        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=2, severities={"high": 1, "medium": 1},
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event == {
            "event": "agent_complete",
            "timestamp": event["timestamp"],
            "agent": "security-reviewer",
            "duration_ms": event["duration_ms"],
            "verdict": "comment",
            "finding_count": 2,
            "severities": {"high": 1, "medium": 1},
            "review_digest": FINAL_DIGEST,
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
        }

    def test_calculates_duration_from_started_file(self, telemetry, output_dir, mod):
        telemetry.start(pr_number="42")
        started_path = Path(started_marker_path(output_dir, "security"))
        started_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        started_path.write_text(started_at.isoformat())
        with _frozen_datetime(mod, started_at + timedelta(milliseconds=50)):
            telemetry.log_agent_complete(
                agent_name="security-reviewer", review_digest=FINAL_DIGEST,
                verdict="approve",
            )
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] == 50

    def test_duration_none_without_started_file(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] is None


class TestReviewVocabularyManifestProjection:
    def test_finalized_summary_and_reconciliation_use_finding_vocabulary(
        self, mod, output_dir, tmp_path
    ):
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        _write_final_review(
            output_dir, "security", canonical_review_document("security", ["medium"])
        )
        reconciliation = {
            "input_finding_count": 3,
            "contributing_agent_count": 2,
            "grouped_concern_count": 2,
            "false_positive_concern_count": 1,
            "out_of_scope_concern_count": 0,
            "verified_concern_count": 1,
            "not_applicable_agents": [],
            "reviewing_agents": ["security-reviewer"],
            "dispatched_agents": ["security-reviewer"],
            "missing_agents": [],
        }
        ledger = canonical_findings_ledger(
            ["medium"], reconciliation=reconciliation
        )
        (output_dir / "review-findings.json").write_text(json.dumps(ledger))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )
        telemetry.start(run_id="run-1")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        summary = manifest["outcome"]["summary"]
        assert summary["total_agent_findings"] == 1
        assert summary["final_finding_count"] == 1
        assert manifest["outcome"]["reconciliation"] == reconciliation
        assert "total_agent_issues" not in json.dumps(manifest)

    def test_missing_reconciliation_is_null_not_an_empty_measurement(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["outcome"]["reconciliation"] is None


# ── _build_summary override counting ────────────────────────────


class TestSummaryOverrideCounting:
    """_build_summary must count DISPATCH_OVERRIDE as dispatched, not skipped."""

    def test_dispatch_override_counted_as_dispatched(self, mod, output_dir, tmp_path):
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "perf-reviewer", "status": "DISPATCH_OVERRIDE"},
                {"name": "a11y-reviewer", "status": "SKIPPED"},
                {"name": "concurrency-reviewer", "status": "SKIPPED_OVERRIDE"},
            ]
        }
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))

        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        summary = t._build_summary(total_duration_ms=10000)

        assert summary["agents_total"] == 4
        assert summary["agents_dispatched"] == 2, \
            "DISPATCH_OVERRIDE should count as dispatched"
        assert summary["agents_skipped"] == 2, \
            "SKIPPED_OVERRIDE should count as skipped, DISPATCH_OVERRIDE should not"

    def test_all_explicit_skipped_statuses_are_counted(
        self, mod, output_dir, tmp_path
    ):
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "SKIPPED"},
                {"name": "perf-reviewer", "status": "SKIPPED_OVERRIDE"},
                {"name": "a11y-reviewer", "status": "SKIPPED_QUICK_MODE"},
                {"name": "security-reviewer", "status": "SKIPPED_TRIAGE"},
            ],
        }
        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps(plan))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        summary = telemetry._build_summary(total_duration_ms=10000)

        assert summary["agents_total"] == 4
        assert summary["agents_dispatched"] == 0
        assert summary["agents_skipped"] == 4


# ── Quick mode + decisions telemetry ──────────────────────────────


class TestQuickModeTelemetry:
    """Quick mode flag and decisions captured in telemetry."""

    def test_quick_mode_flag_reflects_the_passed_value(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42", quick_mode=True)
        assert _read_events(t.log_path)[0]["pipeline"]["quick_mode"] is True

        output_dir2 = tmp_path / "pr-review-org-repo-43"
        output_dir2.mkdir()
        t2 = mod.ReviewTelemetry(str(output_dir2), log_dir=str(log_dir))
        t2.start(pr_number="43")
        assert _read_events(t2.log_path)[0]["pipeline"]["quick_mode"] is False

    def test_log_step_captures_decisions(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        decisions = {"critic_skipped": True, "reason": "quick mode + verdict: comment"}
        t.log_step(step=10, phase="VALIDATION", title="Decision Critic",
                   decisions=decisions)
        step_event = _read_events(t.log_path)[1]
        assert step_event["decisions"] == decisions

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan")
        no_decisions_event = _read_events(t.log_path)[2]
        assert "decisions" not in no_decisions_event

    def test_summary_quick_mode_cross_process(self, mod, tmp_path):
        """Separate ReviewTelemetry instance (simulating different process)
        should still read quick_mode from the JSONL start event."""
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        # Process 1: start() records quick_mode=True
        t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t1.start(pr_number="42", quick_mode=True)
        # Process 2: new instance, never called start()
        t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t2.finalize(step=11, phase="OUTPUT", title="Present Results")
        events = _read_events(t2.log_path)
        final = events[-1]
        assert final["summary"]["quick_mode"] is True


class TestReviewerMarkdownManifest:
    """The manifest records the sanitized reviewer-Markdown outcome."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        telemetry = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        telemetry.start(
            mode="full",
            repo_path=str(tmp_path),
            identifier="branch",
            run_id="run-1",
        )
        return telemetry, out_dir

    def test_state_outcome_is_sanitized_into_manifest(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "reviewer_markdown": {
                "ran": True,
                "written": 2,
                "expected": 3,
                "status": "partial",
                "ignored": "do not persist",
            },
        }))

        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 3,
            "status": "partial",
        }

    def test_malformed_state_outcome_is_unavailable(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "reviewer_markdown": {
                "ran": "yes",
                "written": True,
                "expected": -1,
                "status": "complete",
            },
        }))

        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] is None

    def test_availability_flag_tracks_the_payload(self, mod, tmp_path):
        """Task 13: `availability["reviewer_markdown"]` used to not exist
        at all — the section was written with no flag beside it. It now
        shares the section's own top-level key, derived from whether the
        section actually parsed, the same rule every other optional
        section in `OPTIONAL_SECTION_AVAILABILITY_KEYS` follows."""
        telemetry, out_dir = self._telemetry(mod, tmp_path)

        absent_manifest = json.loads(Path(telemetry.manifest_path).read_text())
        assert absent_manifest["reviewer_markdown"] is None
        assert absent_manifest["availability"]["reviewer_markdown"] is False

        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "reviewer_markdown": {
                "ran": True,
                "written": 1,
                "expected": 1,
                "status": "complete",
            },
        }))
        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        measured_manifest = json.loads(Path(telemetry.manifest_path).read_text())
        assert measured_manifest["availability"]["reviewer_markdown"] is True


class TestFindingsMarkdownManifest:
    """The manifest records the sanitized findings-Markdown outcome —
    `reviewer_markdown`'s sibling family (steps 9 and 11's render of
    `review-findings.md`, versus step 8's per-reviewer render), new in
    Task 13. `build_findings_markdown_manifest` shares its validator with
    `build_reviewer_markdown_manifest`, so this class mirrors
    `TestReviewerMarkdownManifest` field for field, reading
    `state["findings_markdown"]` instead."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        telemetry = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        telemetry.start(
            mode="full",
            repo_path=str(tmp_path),
            identifier="branch",
            run_id="run-1",
        )
        return telemetry, out_dir

    def test_state_outcome_is_sanitized_into_manifest(self, mod, tmp_path):
        """Mirrors `TestReviewerMarkdownManifest` field for field (both
        share `_sanitize_derived_markdown_outcome`), which already covers
        the absent and malformed cases for this validator; this is the
        parity/wiring guard for the second key."""
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "findings_markdown": {
                "ran": True,
                "written": 1,
                "expected": 1,
                "status": "complete",
                "ignored": "do not persist",
            },
        }))

        telemetry.log_step(step=9, phase="VALIDATION", title="Render Findings")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["findings_markdown"] == {
            "ran": True,
            "written": 1,
            "expected": 1,
            "status": "complete",
        }
        assert manifest["availability"]["findings_markdown"] is True


class TestDependencyRefreshManifest:
    """The manifest records the sanitized dependency-refresh report."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    @staticmethod
    def _init_repo(tmp_path):
        """Only `save_report` needs a real git repo (it shells out to
        observe the tracked worktree); every other test in this class
        reads sidecar artifacts directly and needs no repo at all."""
        subprocess.run(
            ["git", "init", str(tmp_path)], check=True, capture_output=True
        )
        (tmp_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tmp_path),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Initial commit",
            ],
            check=True,
            capture_output=True,
        )

    @staticmethod
    def _save_report(out_dir, *, status="completed", commands=None):
        if commands is None:
            commands = [{
                "directory": ".",
                "command": "custom sync --locked",
                "exit_status": "ok",
            }]
        request = out_dir.parent / "dependency-refresh-request.json"
        request.write_text(json.dumps({
            "schema": 1,
            "status": status,
            "commands": commands,
        }))
        assert dependency_refresh.save_report(
            out_dir, request, out_dir.parent
        ) == []

    def test_requested_without_report_is_recorded(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["requested"] is True
        assert section["reported"] is False
        assert "status" not in section

    def test_saved_report_is_projected_into_the_manifest(self, mod, tmp_path):
        self._init_repo(tmp_path)
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        self._save_report(out_dir)
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {
            "requested": True,
            "reported": True,
            "status": "completed",
            "tracked_files_dirty": False,
            "dirty_files": [],
            "commands": [
                {"directory": ".", "command": "custom sync --locked",
                 "exit_status": "ok"},
            ],
        }

    @pytest.mark.parametrize(
        "tracked_files_dirty,dirty_files,expected_precheck",
        [
            pytest.param(
                True, ["tracked.txt"],
                {"tracked_files_dirty": True, "dirty_files": ["tracked.txt"]},
                id="dirty",
            ),
            pytest.param(False, [], None, id="clean"),
        ],
    )
    def test_precheck_projection(
        self, mod, tmp_path, tracked_files_dirty, dirty_files, expected_precheck
    ):
        """`tracked_files_dirty` is a bool-or-None passthrough. `True` and
        `None` both hit the "project the precheck" branch (`dirty`
        represents that branch); `False` is the "omit it" branch (`clean`,
        where the section carries no `precheck` key at all)."""
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "dependency_refresh_precheck": {
                "tracked_files_dirty": tracked_files_dirty,
                "dirty_files": dirty_files,
            },
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        expected = {"requested": True, "reported": False}
        if expected_precheck is not None:
            expected["precheck"] = expected_precheck
        assert section == expected

    def test_malformed_canonical_report_is_unreported_without_replacement(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (_artifact(out_dir, "dependency_refresh")).write_text(json.dumps({
            "status": [],
            "commands": [],
            "tracked_files_dirty": False,
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] == {
            "requested": True,
            "reported": False,
        }
        assert manifest["steps"][-1]["step"] == 5

    def test_saved_report_projects_final_dirty_files(self, mod, tmp_path):
        self._init_repo(tmp_path)
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
        self._save_report(out_dir, status="failed", commands=[{
            "directory": ".",
            "command": "custom sync",
            "exit_status": "failed",
        }])

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["status"] == "failed"
        assert section["tracked_files_dirty"] is True
        assert section["dirty_files"] == ["tracked.txt"]

    @pytest.mark.parametrize(
        "report_bytes",
        [
            b"{not-json",
            json.dumps({
                "commands": [],
                "padding": "x" * (1024 * 1024),
            }).encode("utf-8"),
            (
                b'{"commands":[],"value":'
                + (b"[" * 200000)
                + b"0"
                + (b"]" * 200000)
                + b"}"
            ),
            b"\xff",
            b"[1, 2, 3]",
        ],
        ids=("malformed", "oversized", "deeply-nested", "invalid-utf8", "non-object"),
    )
    def test_hostile_canonical_report_reads_as_unreported(
        self, mod, tmp_path, report_bytes
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (_artifact(out_dir, "dependency_refresh")).write_bytes(report_bytes)

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {"requested": True, "reported": False}

    def test_invalid_report_values_read_as_unreported(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "dependency_refresh")).write_text(json.dumps({
            "status": "did-things",
            "commands": [
                "rm -rf /",                       # not a dict — dropped
                {"directory": 42, "command": ["x"], "exit_status": "great"},
            ],
            "tracked_files_dirty": "yes",
        }))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] is None

    def test_availability_flag_tracks_the_payload(self, mod, tmp_path):
        """Task 13: `availability["dependency_refresh"]` used to not
        exist at all — the section was written with no flag beside it.
        It now shares the section's own top-level key, derived from
        whether the section actually parsed, the same rule every other
        optional section in `OPTIONAL_SECTION_AVAILABILITY_KEYS`
        follows."""
        t, out_dir = self._telemetry(mod, tmp_path)

        absent_manifest = json.loads(Path(t.manifest_path).read_text())
        assert absent_manifest["dependency_refresh"] is None
        assert absent_manifest["availability"]["dependency_refresh"] is False

        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        requested_manifest = json.loads(Path(t.manifest_path).read_text())
        assert requested_manifest["availability"]["dependency_refresh"] is True


class TestWorktreeHygieneManifest:
    """The manifest records the step-11 worktree-hygiene measurement."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    def test_malformed_artifact_yields_none(self, mod, tmp_path):
        (_artifact(tmp_path, "worktree_hygiene")).write_text("[]")
        build = mod.manifest_sections.build_worktree_hygiene_manifest
        assert build(str(tmp_path)) is None

    @pytest.mark.parametrize(
        "payload,expected",
        [
            pytest.param(
                {"schema": 1},
                {
                    "status": "unknown", "new_files": [], "changed_files": [],
                    "probe_residue_removed": [], "baseline_captured_at": None,
                },
                id="missing-fields",
            ),
            pytest.param(
                {
                    "schema": 1, "status": 7,
                    "new_files": ["?? a.txt", 3, None],
                    "changed_files": " M b.txt",
                    "probe_residue_removed": [{"path": "x"}],
                    "baseline_captured_at": 1234,
                },
                {
                    "status": "unknown", "new_files": ["?? a.txt"],
                    "changed_files": [], "probe_residue_removed": [],
                    "baseline_captured_at": None,
                },
                id="non-string-entries",
            ),
        ],
    )
    def test_hygiene_sanitization(self, mod, tmp_path, payload, expected):
        """Missing fields and wrongly-typed entries both degrade to the
        same safe defaults, `status` included — a well-typed status
        outside the allowlist (`test_non_string_entries_are_dropped`'s
        `status: 7`) already exercises the same "unknown" fallback."""
        (_artifact(tmp_path, "worktree_hygiene")).write_text(json.dumps(payload))

        section = mod.manifest_sections.build_worktree_hygiene_manifest(
            str(tmp_path)
        )

        assert section == expected

    def test_measured_unknown_is_not_absent(self, mod, tmp_path):
        """A measured "unknown" is a section; only an absent artifact is None."""
        t, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "worktree_hygiene")).write_text(json.dumps({
            "schema": 1,
            "status": "unknown",
            "new_files": [],
            "changed_files": [],
            "probe_residue_removed": [],
            "baseline_captured_at": None,
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"]["status"] == "unknown"
        assert manifest["availability"]["worktree_hygiene"] is True

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "worktree_hygiene")).write_text(json.dumps({
            "schema": 1,
            "status": "changed_during_review",
            "new_files": ["?? notes.md"],
            "changed_files": [" M src/app.py"],
            "probe_residue_removed": ["zz_pirategoat-probe.go"],
            "baseline_captured_at": "2026-08-19T10:00:00+00:00",
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"] == {
            "status": "changed_during_review",
            "new_files": ["?? notes.md"],
            "changed_files": [" M src/app.py"],
            "probe_residue_removed": ["zz_pirategoat-probe.go"],
            "baseline_captured_at": "2026-08-19T10:00:00+00:00",
        }
        assert manifest["availability"]["worktree_hygiene"] is True

    def test_absent_artifact_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"] is None
        assert manifest["availability"]["worktree_hygiene"] is False


class TestUsageManifest:
    """The manifest records the step-11 token-usage snapshot.

    The snapshot has two halves with independent warrants: subagent
    transcripts are closed at capture time and can read "complete", while
    the orchestrator is measuring its own still-open session. The
    projection must preserve that split rather than flattening it into one
    "usage was measured" bit.
    """

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    def _usage(self, output=7):
        return {
            "input_tokens": 1,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
            "effective_input_tokens": 6,
            "output_tokens": output,
        }

    def _snapshot(self, **overrides):
        snapshot = {
            "schema": 1,
            "captured_at": "2026-08-19T10:43:00+00:00",
            "window": {"started_at": "2026-08-19T10:00:00+00:00",
                       "ended_at": "2026-08-19T10:43:00+00:00",
                       "closed": False},
            "availability": {"subagents": "complete",
                             "orchestrator": "partial"},
            "reason": None,
            "agents_measured": {"measured": 2, "expected": 2},
            "subagent_usage": [
                {"agent": "code-reviewer", "model": "claude-opus-5[1m]",
                 "usage": self._usage(output=5)},
                {"agent": "security-reviewer", "model": "claude-sonnet-5",
                 "usage": self._usage(output=2)},
            ],
            "subagent_totals": self._usage(),
            "usage_by_model": {"claude-opus-5[1m]": self._usage(output=5),
                               "claude-sonnet-5": self._usage(output=2)},
            "orchestrator_usage": self._usage(output=9),
        }
        snapshot.update(overrides)
        return snapshot

    def _write(self, output_dir, snapshot):
        (_artifact(output_dir, "usage_snapshot")).write_text(
            json.dumps(snapshot)
        )

    def test_agent_tool_and_read_counts_are_projected_without_inventing_them(
        self, mod, tmp_path
    ):
        snapshot = self._snapshot(subagent_usage=[
            {
                "agent": "code-reviewer",
                "model": "claude-opus-5[1m]",
                "usage": self._usage(output=5),
                "tool_calls": 42,
                "repository_reads": 7,
            },
            {
                "agent": "security-reviewer",
                "model": "claude-sonnet-5",
                "usage": self._usage(output=2),
                "tool_calls": None,
                "repository_reads": 3,
            },
        ])
        self._write(tmp_path, snapshot)

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["by_agent"][0]["tool_calls"] == 42
        assert section["by_agent"][0]["repository_reads"] == 7
        assert section["by_agent"][1]["tool_calls"] is None
        assert section["by_agent"][1]["repository_reads"] == 3

    @pytest.mark.parametrize(
        "raw_text",
        [
            pytest.param("[]", id="malformed-json"),
            pytest.param(None, id="unknown-schema"),
        ],
    )
    def test_usage_unmeasured(self, mod, tmp_path, raw_text):
        """Malformed JSON and a schema this builder does not know (a
        missing schema key hits the same `schema != 1` check) are both
        evidence this builder cannot vouch for."""
        if raw_text is not None:
            (_artifact(tmp_path, "usage_snapshot")).write_text(raw_text)
        else:
            self._write(tmp_path, self._snapshot(schema=2))

        build = mod.manifest_sections.build_usage_manifest
        assert build(str(tmp_path)) is None

    def test_a_closed_window_is_projected_as_closed(self, mod, tmp_path):
        """The flag is what separates "partial because the run was still
        open" from "partial because the evidence was damaged"."""
        self._write(tmp_path, self._snapshot(
            window={"started_at": "2026-08-19T10:00:00+00:00",
                    "ended_at": "2026-08-19T10:43:05+00:00",
                    "closed": True},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["window"]["closed"] is True

    @pytest.mark.parametrize(
        "window",
        [{}, {"closed": 1}],
        ids=["absent", "int"],
    )
    def test_unreadable_window_falls_to_substituted(self, mod, tmp_path,
                                                    window):
        """"closed" is the stronger claim, so an unreadable flag must fall
        to the weaker one rather than license the stronger. `int` also
        guards the `1 == True` trap: a bare `is True` would wrongly accept
        a truthy `1`."""
        self._write(tmp_path, self._snapshot(window=window))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["window"] == {
            "started_at": None, "ended_at": None, "closed": False,
        }

    def test_measured_missing_is_not_absent(self, mod, tmp_path):
        """A run that tried and found no transcripts is a section, not None.

        Only a run that never attempted the capture has no artifact — the
        same distinction hygiene draws between a measured "unknown" and an
        absent measurement.
        """
        self._write(tmp_path, self._snapshot(
            availability={"subagents": "missing", "orchestrator": "missing"},
            reason="missing_session_id",
            agents_measured={"measured": 0, "expected": None},
            subagent_usage=[],
            subagent_totals=None,
            usage_by_model=None,
            orchestrator_usage=None,
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section is not None
        assert section["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }
        assert section["subagent_totals"] is None
        assert section["orchestrator_usage"] is None
        assert section["usage_by_model"] == {}
        assert section["by_agent"] == []
        assert section["agents_measured"] == {"measured": 0, "expected": None}

    def test_unrecognized_availability_degrades_to_missing(self, mod, tmp_path):
        """A well-typed label outside the vocabulary is not a measurement."""
        self._write(tmp_path, self._snapshot(
            availability={"subagents": "excellent", "orchestrator": 7},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }

    def test_damaged_usage_maps_are_dropped_not_zeroed(self, mod, tmp_path):
        """A partially typed usage map is unusable evidence, not a zero."""
        self._write(tmp_path, self._snapshot(
            subagent_totals={"output_tokens": "lots"},
            orchestrator_usage={"output_tokens": 4},
            # JSON keys are always strings, so only the value side of
            # a model bucket can be damaged.
            usage_by_model={"claude-sonnet-5": None},
            subagent_usage=[
                {"agent": "code-reviewer", "model": 5, "usage": self._usage()},
                {"agent": 7, "model": "x", "usage": self._usage()},
                "not-a-row",
            ],
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["subagent_totals"] is None
        assert section["orchestrator_usage"] is None
        assert section["usage_by_model"] == {}
        assert section["by_agent"] == [
            {"agent": "code-reviewer", "model": None, "usage": self._usage(),
             "tool_calls": None, "repository_reads": None},
        ]

    def test_non_integer_agent_counts_are_dropped(self, mod, tmp_path):
        self._write(tmp_path, self._snapshot(
            agents_measured={"measured": True, "expected": -1},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["agents_measured"] == {
            "measured": None, "expected": None,
        }

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        self._write(out_dir, self._snapshot())

        t.finalize(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        section = manifest["usage"]
        assert section["captured_at"] == "2026-08-19T10:43:00+00:00"
        assert section["window"] == {
            "started_at": "2026-08-19T10:00:00+00:00",
            "ended_at": "2026-08-19T10:43:00+00:00",
            "closed": False,
        }
        assert section["availability"] == {
            "subagents": "complete", "orchestrator": "partial",
        }
        assert section["agents_measured"] == {"measured": 2, "expected": 2}
        assert section["subagent_totals"]["output_tokens"] == 7
        assert section["orchestrator_usage"]["output_tokens"] == 9
        assert section["usage_by_model"]["claude-opus-5[1m]"][
            "output_tokens"] == 5
        assert section["by_agent"] == [
            {"agent": "code-reviewer", "model": "claude-opus-5[1m]",
             "usage": self._usage(output=5), "tool_calls": None,
             "repository_reads": None},
            {"agent": "security-reviewer", "model": "claude-sonnet-5",
             "usage": self._usage(output=2), "tool_calls": None,
             "repository_reads": None},
        ]
        assert manifest["availability"]["usage"] is True

    def test_absent_artifact_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["usage"] is None
        assert manifest["availability"]["usage"] is False


class TestSkippedStepsManifest:
    """The manifest records the step-skip decisions the router made."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param(None, id="missing-file"),
            pytest.param({"skipped_steps": {"step": 2}}, id="non-list-value"),
        ],
    )
    def test_skipped_steps_unmeasured(self, mod, tmp_path, state):
        """A missing `pipeline_state.json`, a state without the key, a
        malformed (non-object) state, and a non-list `skipped_steps` value
        all reach the same `not isinstance(value, list) -> None` guard;
        `missing-file` and `non-list-value` represent the family."""
        if state is not None:
            (_artifact(tmp_path, "pipeline_state")).write_text(json.dumps(state))
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) is None

    def test_unusable_entries_are_dropped_and_fields_default(
        self, mod, tmp_path
    ):
        """Only step-identified records survive; absent prose reads empty."""
        (_artifact(tmp_path, "pipeline_state")).write_text(json.dumps({
            "skipped_steps": [
                {"step": 4},
                {"step": "12", "title": "Cleanup"},
                "step 2",
                {"title": "Repo Setup"},
                {"step": True, "title": "Bool Is Not A Step"},
            ],
        }))

        section = mod.manifest_sections.build_skipped_steps_manifest(
            str(tmp_path)
        )

        assert section == [{"step": 4, "title": "", "condition": ""}]

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "skipped_steps": [
                {"step": 2, "title": "Repo Setup",
                 "condition": "needs_workspace_setup"},
                {"step": 12, "title": "Cleanup",
                 "condition": "has_workspace_state_interactive"},
            ],
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] == [
            {"step": 2, "title": "Repo Setup",
             "condition": "needs_workspace_setup"},
            {"step": 12, "title": "Cleanup",
             "condition": "has_workspace_state_interactive"},
        ]
        assert manifest["availability"]["skipped_steps"] is True

    def test_measured_zero_skips_is_available(self, mod, tmp_path):
        """[] is a measured result, not an absent measurement."""
        t, out_dir = self._telemetry(mod, tmp_path)
        (_artifact(out_dir, "pipeline_state")).write_text(json.dumps({
            "skipped_steps": [],
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] == []
        assert manifest["availability"]["skipped_steps"] is True

    def test_absent_state_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] is None
        assert manifest["availability"]["skipped_steps"] is False


class TestSynthesisAgentsManifest:
    """The manifest records the reconciliator/critic lifecycle.

    A family of its own, never folded into `manifest["agents"]`: those two
    agents are never in a dispatch plan and produce no reviewer lifecycle
    events, so mixing them in would corrupt every reviewer count
    downstream. The three outcomes this projection keeps apart are `None`
    (never measured — every run predating the feature), a measured empty
    list (finalize looked and found no dispatch markers), and the rows.
    """

    RECONCILIATOR = lifecycle_contract.RECONCILIATOR
    CRITIC = lifecycle_contract.DECISION_CRITIC

    def _row(self, agent, **overrides):
        row = {
            "agent": agent,
            "verdict": (
                "request_changes" if agent == self.RECONCILIATOR else "STAND"
            ),
            "started_at": "2026-08-19T12:00:00+00:00",
            "completed_at": "2026-08-19T12:11:05+00:00",
            "duration_ms": 665_000,
            "stalled": False,
        }
        row.update(overrides)
        return row

    def _write(self, tmp_path, payload):
        (_artifact(tmp_path, "synthesis_agents")).write_text(json.dumps(payload))

    def _artifact(self, *rows, **overrides):
        payload = {
            "schema": 1,
            "finalized": True,
            "agents": list(rows),
        }
        payload.update(overrides)
        return payload

    def _build(self, mod, tmp_path):
        return mod.manifest_sections.build_synthesis_agents_manifest(
            str(tmp_path)
        )

    def test_unknown_schema_is_unmeasured(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC), schema=2,
        ))
        assert self._build(mod, tmp_path) is None

    def test_boolean_schema_is_unmeasured(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC), schema=True,
        ))
        assert self._build(mod, tmp_path) is None

    def test_measured_empty_is_not_absent(self, mod, tmp_path):
        """Finalize ran and found no dispatch markers. That is a measured
        zero dispatches, not an unmeasured run."""
        self._write(tmp_path, self._artifact())
        assert self._build(mod, tmp_path) == {
            "finalized": True,
            "agents": [],
        }

    def test_durations_project_intact(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(self._row(self.CRITIC)))
        section = self._build(mod, tmp_path)
        assert section["agents"] == [self._row(self.CRITIC)]

    def test_stall_projects_as_stalled_without_a_duration(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(self._row(
            self.RECONCILIATOR, completed_at=None, duration_ms=None,
            stalled=True,
        )))
        row = self._build(mod, tmp_path)["agents"][0]
        assert row["stalled"] is True
        assert row["duration_ms"] is None

    @pytest.mark.parametrize(
        "value", [1, "true"],
        ids=["int", "truthy-string"],
    )
    def test_only_an_explicit_true_reads_as_stalled(self, mod, tmp_path, value):
        """A stall accuses the run. An unreadable flag does not license
        that claim — same rule usage's `window.closed` follows. `int`
        also guards the `1 == True` trap."""
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC, stalled=value)
        ))
        assert self._build(mod, tmp_path)["agents"][0]["stalled"] is False

    @pytest.mark.parametrize(
        "value", [-1, True],
        ids=["negative", "bool"],
    )
    def test_unusable_duration_is_none_never_zero(self, mod, tmp_path, value):
        """A duration that cannot be read is absent. Zeroing it would
        publish "the phase finished instantly"."""
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC, duration_ms=value)
        ))
        assert self._build(mod, tmp_path)["agents"][0]["duration_ms"] is None

    def test_rows_without_a_named_agent_are_dropped(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            {"duration_ms": 5}, "not-a-row", self._row(self.CRITIC),
        ))
        section = self._build(mod, tmp_path)
        assert [row["agent"] for row in section["agents"]] == [self.CRITIC]

    def test_non_list_agents_projects_measured_empty(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(agents="nope"))
        assert self._build(mod, tmp_path)["agents"] == []

    def test_manifest_carries_the_section_and_its_availability(
        self, mod, tmp_path
    ):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(tmp_path / "logs"))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")

        manifest = _read_manifest(t)
        assert manifest["synthesis_agents"] is None
        assert manifest["availability"]["synthesis_agents"] is False

        self._write(out_dir, self._artifact(self._row(self.CRITIC)))
        t.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(t)
        assert manifest["availability"]["synthesis_agents"] is True
        assert manifest["synthesis_agents"]["agents"][0]["duration_ms"] == (
            665_000
        )

    def test_reviewer_lifecycle_is_untouched_by_synthesis_rows(
        self, mod, tmp_path
    ):
        """The non-interference pin: a full reviewer cohort's started /
        completed / incomplete projection must be byte-identical whether
        or not the synthesis section exists beside it."""
        def build(out_dir):
            t = mod.ReviewTelemetry(
                str(out_dir), log_dir=str(out_dir / "logs")
            )
            t.start(mode="full", repo_path=str(tmp_path),
                    identifier="branch", run_id="run-1")
            for index in range(19):
                name = f"agent-{index:02d}-reviewer"
                t.log_agent_start(name, domain="code", model_tier="sonnet")
                t.log_agent_complete(
                    name, review_digest=FINAL_DIGEST,
                    verdict="approve", finding_count=0,
                )
            t.finalize(step=11, phase="OUTPUT", title="Present Results")
            return _read_manifest(t)

        plain = tmp_path / "plain"
        plain.mkdir()
        baseline = build(plain)

        beside = tmp_path / "beside"
        beside.mkdir()
        self._write(beside, self._artifact(
            self._row(self.RECONCILIATOR), self._row(self.CRITIC),
        ))
        with_synthesis = build(beside)

        assert len(baseline["agents"]["started"]) == 19
        assert len(baseline["agents"]["completed"]) == 19
        assert baseline["agents"]["incomplete"] == []

        def scrub(events):
            return [
                {k: v for k, v in event.items()
                 if k not in ("timestamp", "duration_ms")}
                for event in events
            ]

        for key in ("started", "completed"):
            assert scrub(with_synthesis["agents"][key]) == scrub(
                baseline["agents"][key]
            )
        assert with_synthesis["agents"]["incomplete"] == (
            baseline["agents"]["incomplete"]
        )
        assert with_synthesis["synthesis_agents"] is not None
        assert baseline["synthesis_agents"] is None


class TestSynthesisAgentsManifestShape:
    """The section self-describes, and its row shape is declared once."""

    def _build(self, mod, tmp_path, payload):
        (_artifact(tmp_path, "synthesis_agents")).write_text(json.dumps(payload))
        return mod.manifest_sections.build_synthesis_agents_manifest(
            str(tmp_path)
        )

    def _artifact(self, *rows):
        return {
            "schema": 1,
            "finalized": True,
            "agents": list(rows),
        }

    def _row(self, **overrides):
        row = {
            key: None for key in lifecycle_contract.ROW_KEYS
        }
        row.update({
            "agent": lifecycle_contract.DECISION_CRITIC,
            "verdict": "STAND",
            "duration_ms": 665_000,
            "stalled": False,
        })
        row.update(overrides)
        return row

    def test_builder_covers_exactly_the_declared_row_keys(self, mod, tmp_path):
        """Row-shape parity, producer side. Three modules write this
        shape; teaching only one of them must fail loudly. The same
        `set(row) == set(ROW_KEYS)` equality already rejects an
        undeclared key sneaking in beside the declared ones."""
        section = self._build(
            mod, tmp_path, self._artifact(self._row(invented_key="x"))
        )
        assert set(section["agents"][0]) == set(lifecycle_contract.ROW_KEYS)

    def test_an_unusable_verdict_is_none(self, mod, tmp_path):
        """`test_durations_project_intact` already asserts the whole row
        (verdict included) for a good value; only an unusable one needs
        its own case."""
        section = self._build(
            mod, tmp_path, self._artifact(self._row(verdict=None))
        )
        assert section["agents"][0]["verdict"] is None


class TestOptionalSectionAvailabilityKeysContract:
    """I2: the producer-declared contract, restated at the producer.

    `OPTIONAL_SECTION_AVAILABILITY_KEYS` names every optional section
    `_build_manifest` ever assigns into `availability`. This pins that
    claim in both directions against what the method actually produces —
    minus `pipeline`/`transcript`, the two structurally-always-present
    keys `_build_manifest` sets before any optional section runs, neither
    of which is optional or shares a same-named top-level section.

    A future engineer wiring a new section's availability flag directly
    into `_build_manifest` without adding it to the tuple (the reviewer's
    original probe scenario: Task 13 closed it for `dependency_refresh`
    and `reviewer_markdown`, the two sections that used to lack a flag —
    the probe below now simulates the same gap with a section name that
    stays permanently fictional) fails THIS assertion — not the
    consumer-side sanitize pin three call frames away in
    `review_metrics`, and not silently.
    """

    def test_produced_keys_equal_the_declared_tuple_both_directions(
        self, mod, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        produced = set(manifest["availability"]) - {"pipeline", "transcript"}
        declared = set(mod.OPTIONAL_SECTION_AVAILABILITY_KEYS)

        assert produced == declared, (
            f"_build_manifest's availability keys {sorted(produced)} != "
            f"the declared OPTIONAL_SECTION_AVAILABILITY_KEYS "
            f"{sorted(declared)} — update the tuple in telemetry.py (and "
            "its sanitizer in review_metrics/sanitize.py's "
            "_OPTIONAL_SECTION_SANITIZERS) when a section's availability "
            "wiring changes."
        )


class TestReprojectUsage:
    """`ReviewTelemetry.reproject_usage()` — the manifest's own patch path
    for a `usage_snapshot.py` re-run that happens out of band, long after
    `finalize()` already returned. Telemetry keeps ONE owning module for
    the manifest even with two call sites into it: the normal event-driven
    rebuild in `_materialize_manifest`, and this narrow out-of-band patch.
    """

    def _seed_snapshot(self, output_dir, **overrides):
        payload = {
            "schema": 1,
            "captured_at": "2026-08-19T10:43:00+00:00",
            "window": {
                "started_at": "2026-08-19T10:00:00+00:00",
                "ended_at": "2026-08-19T10:43:00+00:00",
                "closed": True,
            },
            "availability": {
                "subagents": "complete", "orchestrator": "complete",
            },
            "reason": None,
            "agents_measured": {"measured": 1, "expected": 1},
            "subagent_usage": [],
            "subagent_totals": None,
            "usage_by_model": None,
            "orchestrator_usage": None,
        }
        payload.update(overrides)
        (_artifact(output_dir, "usage_snapshot")).write_text(
            json.dumps(payload)
        )

    @staticmethod
    def _strip_usage(manifest):
        """The residual: everything a `usage` patch has no license to touch."""
        stripped = dict(manifest)
        stripped.pop("usage", None)
        availability = dict(stripped.get("availability") or {})
        availability.pop("usage", None)
        stripped["availability"] = availability
        return stripped

    def _fully_populated_manifest(self, mod, output_dir):
        """Every optional section carries real, distinguishable content —
        the shape a full `_build_manifest` rebuild from THIS instance's
        actual (near-empty) JSONL log would NOT reproduce. A
        `reproject_usage()` that reconstructed the whole manifest instead
        of surgically patching two keys — the reviewer's mutation (d) —
        would replace every one of these with the rebuild's own (emptier)
        values; a correct surgical patch leaves them exactly as written
        here.
        """
        return {
            "schema": mod.EVENT_SCHEMA,
            "status": "complete",
            "run": {
                "id": "run-1",
                "session_id": "session-fixture",
                "plugin_version": "9.9.9",
                "mode": "pr",
                "repo_path": "/fixture/repo",
                "output_dir": str(output_dir),
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T01:00:00+00:00",
                "git": {
                    "requested_range": "base..head",
                    "base_sha": "a" * 40,
                    "head_sha": "b" * 40,
                },
            },
            "steps": [
                {"step": 99, "phase": "FIXTURE", "title": "Vandal Probe"}
            ],
            "agents": {
                "started": [{"agent": "fixture-reviewer"}],
                "completed": [],
                "incomplete": [],
            },
            "dispatch": {"fixture": "dispatch-payload"},
            "assignment": {"fixture": "assignment-payload"},
            "outcome": {"summary": {"fixture": "outcome-payload"}},
            "availability": {
                "pipeline": True,
                "transcript": True,
                "assignment": True,
                "worktree_hygiene": True,
                "synthesis_agents": True,
                "usage": True,
                "skipped_steps": True,
                "dependency_refresh": True,
                "reviewer_markdown": True,
                "findings_markdown": True,
            },
            "worktree_hygiene": {"fixture": "hygiene-payload"},
            "synthesis_agents": {"fixture": "synthesis-payload"},
            "usage": {"fixture": "stale — must be replaced"},
            "skipped_steps": [{"fixture": "skip-payload"}],
            "dependency_refresh": {"fixture": "deps-payload"},
            "reviewer_markdown": {"fixture": "markdown-payload"},
            "findings_markdown": {"fixture": "findings-payload"},
        }

    def test_patches_usage_and_leaves_every_other_section_byte_identical(
        self, mod, telemetry, output_dir
    ):
        """I2/M2/M4 residual pin. The reviewer's mutation (d) — a
        `reproject_usage()` that rebuilds the whole manifest rather than
        patching two keys — silently vandalized seven other optional
        sections and still passed the full 5093-test suite, because no
        existing fixture carried real content in all of them at once.
        This one does, and asserts the residual (everything but `usage`
        and `availability.usage`) survives byte-identical.
        """
        telemetry.start(run_id="run-1", session_id="session-real")
        manifest_path = Path(telemetry.manifest_path)
        fixture = self._fully_populated_manifest(mod, output_dir)
        manifest_path.write_text(json.dumps(fixture))
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "written"
        after = json.loads(manifest_path.read_text())
        assert self._strip_usage(after) == self._strip_usage(fixture)
        assert after["usage"] is not None
        assert after["usage"] != fixture["usage"]
        assert after["availability"]["usage"] is True

    def test_running_manifest_is_left_untouched(
        self, mod, telemetry, output_dir
    ):
        """M2 gate: a still-running manifest is `finalize()`'s territory
        alone. The in-pipeline step-11 call reaches this method while the
        manifest still reads "running" (finalize has not appended
        `pipeline_end` yet), so it is a no-op there every time —
        `finalize()`'s own full rebuild, moments later in the same run,
        is what actually settles `usage` for a normal pipeline run.
        """
        telemetry.start(run_id="run-1")  # status stays "running"
        manifest_path = Path(telemetry.manifest_path)
        before_bytes = manifest_path.read_bytes()
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "not_settled"
        assert manifest_path.read_bytes() == before_bytes

    def test_unsupported_schema_manifest_is_left_untouched(
        self, mod, telemetry, output_dir
    ):
        """M4 gate: an unsupported-schema manifest is not this method's
        to interpret."""
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "complete"
        manifest["schema"] = mod.EVENT_SCHEMA - 1
        manifest_path.write_text(json.dumps(manifest))
        before_bytes = manifest_path.read_bytes()
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "unsupported_schema"
        assert manifest_path.read_bytes() == before_bytes

    def test_no_manifest_is_a_silent_no_op(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs-none"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))

        assert t.reproject_usage() == "absent"

    def test_unreadable_snapshot_still_reprojects_an_honest_absence(
        self, mod, telemetry, output_dir
    ):
        """A settled manifest whose `usage-snapshot.json` cannot be read
        still gets patched — to `usage: None`, `availability.usage:
        False` — because that IS the current truth, not a reason to skip
        the write."""
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "complete"
        manifest_path.write_text(json.dumps(manifest))
        # No usage-snapshot.json written at all.

        result = telemetry.reproject_usage()

        after = json.loads(manifest_path.read_text())
        assert result == "written"
        assert after["usage"] is None
        assert after["availability"]["usage"] is False

    def test_a_corrupt_marker_reports_io_failure_never_raises(
        self, mod, output_dir, tmp_path
    ):
        """The marker read behind `manifest_path` raises on invalid bytes;
        the method must answer, not traceback — the CLI calls it after the
        snapshot already wrote, and a raise would cost the whole summary
        (regression: the first cut read the property unguarded)."""
        log_dir = tmp_path / "logs-corrupt"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        marker = _artifact(output_dir, "telemetry_log_path")
        marker.write_bytes(b"\xff\xfe not utf-8")

        assert t.reproject_usage() == "io_failure"

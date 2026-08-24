"""Reviewer candidate publication and immutable finalization contracts."""

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import ReviewOutputBuilder, finalize_candidate
from review.telemetry import ReviewTelemetry


def _write_sidecar(output_dir, reviewer="code", agent_name=None):
    Path(output_dir, f"{reviewer}-deferred-files.json").write_text(
        json.dumps({
            "schema": 2,
            "agent_name": agent_name or f"{reviewer}-reviewer",
            "deferred_files": ["src/deferred.py"],
            "diffed_count": 1,
            "in_scope_count": 2,
        })
    )


def _builder(issue_count=0, reviewer="code"):
    builder = ReviewOutputBuilder(pr_id="42", reviewer=reviewer)
    for index in range(issue_count):
        builder.add_issue(
            severity="low",
            title=f"Finding {index}",
            file="src/code.py",
            line=index + 1,
            description="Description",
            recommendation="Recommendation",
        )
    return builder


def _start_telemetry(tmp_path, output_dir):
    telemetry = ReviewTelemetry(
        str(output_dir), log_dir=str(tmp_path / "logs")
    )
    telemetry.start(run_id="run-42")
    telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
    return telemetry


class TestCandidatePublication:
    def test_save_is_candidate_until_explicit_finalize(self, tmp_path):
        _write_sidecar(tmp_path)

        result = _builder().save(str(tmp_path))

        assert result["candidate"].endswith("code-review.candidate.json")
        assert not (tmp_path / "code-review.json").exists()

        finalized = finalize_candidate(
            str(tmp_path), "code", result["candidate_digest"]
        )

        assert finalized["json"].endswith("code-review.json")
        assert (tmp_path / "code-review.json").exists()
        assert not (tmp_path / "code-review.candidate.json").exists()

    def test_second_save_replaces_candidate_and_changes_digest(self, tmp_path):
        _write_sidecar(tmp_path)
        first = _builder(issue_count=0).save(str(tmp_path))
        first_bytes = (tmp_path / "code-review.candidate.json").read_bytes()

        second = _builder(issue_count=1).save(str(tmp_path))
        second_bytes = (tmp_path / "code-review.candidate.json").read_bytes()

        assert first["candidate_digest"] != second["candidate_digest"]
        assert first_bytes != second_bytes
        assert second["candidate_digest"] == hashlib.sha256(second_bytes).hexdigest()
        assert json.loads(second_bytes)["summary"]["total_issues"] == 1

    def test_digest_mismatch_never_publishes_an_overlapping_save(self, tmp_path):
        _write_sidecar(tmp_path)
        observed = _builder(issue_count=0).save(str(tmp_path))
        latest = _builder(issue_count=1).save(str(tmp_path))

        with pytest.raises(ValueError, match="digest"):
            finalize_candidate(
                str(tmp_path), "code", observed["candidate_digest"]
            )

        assert latest["candidate_digest"] != observed["candidate_digest"]
        assert not (tmp_path / "code-review.json").exists()
        assert (tmp_path / "code-review.candidate.json").exists()

    def test_save_after_finalization_is_rejected_without_mutating_canonical(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)
        saved = _builder().save(str(tmp_path))
        finalize_candidate(str(tmp_path), "code", saved["candidate_digest"])
        canonical = tmp_path / "code-review.json"
        before = canonical.read_bytes()

        with pytest.raises(ValueError, match="finalized"):
            _builder(issue_count=1).save(str(tmp_path))

        assert canonical.read_bytes() == before
        assert not (tmp_path / "code-review.candidate.json").exists()

    def test_manually_edited_candidate_is_rejected_before_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        result = _builder().save(str(tmp_path))
        candidate = tmp_path / "code-review.candidate.json"
        data = json.loads(candidate.read_text())
        data["reviewer"] = "security"
        edited = json.dumps(data).encode()
        candidate.write_bytes(edited)

        with pytest.raises(ValueError, match="reviewer"):
            finalize_candidate(
                str(tmp_path), "code", hashlib.sha256(edited).hexdigest()
            )

        assert result["candidate_digest"] != hashlib.sha256(edited).hexdigest()
        assert not (tmp_path / "code-review.json").exists()

    def test_malformed_candidate_is_rejected_before_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        candidate = tmp_path / "code-review.candidate.json"
        malformed = b"{not json"
        candidate.write_bytes(malformed)

        with pytest.raises(ValueError, match="malformed review candidate"):
            finalize_candidate(
                str(tmp_path), "code", hashlib.sha256(malformed).hexdigest()
            )

        assert not (tmp_path / "code-review.json").exists()

    def test_edited_derived_coverage_is_rejected_before_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        candidate = tmp_path / "code-review.candidate.json"
        data = json.loads(candidate.read_text())
        data["meta"]["files_reviewed"] = 99
        edited = json.dumps(data).encode()
        candidate.write_bytes(edited)

        with pytest.raises(ValueError, match="derived coverage"):
            finalize_candidate(
                str(tmp_path), "code", hashlib.sha256(edited).hexdigest()
            )

        assert not (tmp_path / "code-review.json").exists()

    def test_closed_intake_rejects_candidate_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        (tmp_path / "review-intake.json").write_text(
            json.dumps({"schema": 1, "closed_at": "2026-08-24T12:00:00+00:00"})
        )

        with pytest.raises(ValueError, match="intake"):
            _builder().save(str(tmp_path))

        assert not (tmp_path / "code-review.candidate.json").exists()

    def test_closed_intake_rejects_finalization_without_losing_candidate(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)
        saved = _builder().save(str(tmp_path))
        (tmp_path / "review-intake.json").write_text(
            json.dumps({"schema": 1, "closed_at": "2026-08-24T12:00:00+00:00"})
        )

        with pytest.raises(ValueError, match="intake"):
            finalize_candidate(
                str(tmp_path), "code", saved["candidate_digest"]
            )

        assert (tmp_path / "code-review.candidate.json").exists()
        assert not (tmp_path / "code-review.json").exists()

    def test_candidate_telemetry_failure_warns_after_successful_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        import review.agent.output as output_mod

        _write_sidecar(tmp_path)

        def _boom(*args, **kwargs):
            raise OSError("diagnostic telemetry unavailable")

        monkeypatch.setattr(output_mod, "_log_agent_save_telemetry", _boom)
        result = _builder().save(str(tmp_path))
        captured = capsys.readouterr()

        assert Path(result["candidate"]).exists()
        assert result["candidate_digest"] in captured.out
        assert "candidate published" in captured.err

    def test_finalize_cli_rejects_wrong_digest_then_publishes_exact_candidate(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)
        saved = _builder().save(str(tmp_path))
        output_py = SCRIPTS_DIR / "review" / "agent" / "output.py"

        rejected = subprocess.run(
            [
                sys.executable, str(output_py), "finalize",
                "--output-dir", str(tmp_path), "--reviewer", "code",
                "--candidate-digest", "0" * 64,
            ],
            capture_output=True, text=True, cwd=tmp_path,
        )
        finalized = subprocess.run(
            [
                sys.executable, str(output_py), "finalize",
                "--output-dir", str(tmp_path), "--reviewer", "code",
                "--candidate-digest", saved["candidate_digest"],
            ],
            capture_output=True, text=True, cwd=tmp_path,
        )
        retried = subprocess.run(
            [
                sys.executable, str(output_py), "finalize",
                "--output-dir", str(tmp_path), "--reviewer", "code",
                "--candidate-digest", saved["candidate_digest"],
            ],
            capture_output=True, text=True, cwd=tmp_path,
        )

        assert rejected.returncode == 1
        assert "REJECTED" in rejected.stderr
        assert finalized.returncode == 0
        assert "FINALIZED" in finalized.stdout
        assert retried.returncode == 0
        assert "ALREADY FINALIZED" in retried.stdout
        assert (tmp_path / "code-review.json").exists()

    def test_concurrent_same_reviewer_saves_leave_one_complete_candidate(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda count: _builder(issue_count=count).save(str(tmp_path)),
                (1, 2),
            ))

        candidate = tmp_path / "code-review.candidate.json"
        candidate_bytes = candidate.read_bytes()
        candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
        assert candidate_digest in {result["candidate_digest"] for result in results}
        assert json.loads(candidate_bytes)["summary"]["total_issues"] in {1, 2}
        assert not list(tmp_path.glob("*.tmp"))

    def test_failed_candidate_replace_cleans_nonce_staging_file(
        self, tmp_path, monkeypatch
    ):
        import review.agent.output as output_mod

        _write_sidecar(tmp_path)

        def _fail_replace(*args, **kwargs):
            raise OSError("replace unavailable")

        monkeypatch.setattr(output_mod.os, "replace", _fail_replace)

        with pytest.raises(OSError, match="replace unavailable"):
            _builder().save(str(tmp_path))

        assert not (tmp_path / "code-review.candidate.json").exists()
        assert not list(tmp_path.glob("code-review.candidate.json.*.tmp"))


class TestFinalizationTelemetry:
    def test_two_saves_and_one_finalization_have_split_event_semantics(
        self, tmp_path
    ):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = _start_telemetry(tmp_path, output_dir)
        _write_sidecar(output_dir)

        _builder(issue_count=0).save(str(output_dir))
        saved = _builder(issue_count=1).save(str(output_dir))
        finalize_candidate(str(output_dir), "code", saved["candidate_digest"])
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        events = telemetry._read_events()
        assert [event["event"] for event in events].count("agent_save") == 2
        assert [event["event"] for event in events].count("agent_complete") == 1
        [complete] = [event for event in events if event["event"] == "agent_complete"]
        assert complete["artifact_digest"] == saved["candidate_digest"]

        manifest = json.loads(Path(telemetry.manifest_path).read_text())
        [projected] = manifest["agents"]["completed"]
        assert projected["verdict"] == "approve"
        assert projected["issue_count"] == 1
        assert projected["artifact_digest"] == saved["candidate_digest"]

    def test_finalization_uses_exact_repo_adapter_instance_identity(self, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )
        telemetry.start(run_id="run-42")
        agent_name = "repo-api-reviewer-v2-reviewer"
        telemetry.log_agent_start(agent_name=agent_name, domain="")
        reviewer = "repo-api-reviewer-v2"
        _write_sidecar(output_dir, reviewer=reviewer, agent_name=agent_name)

        saved = _builder(reviewer=reviewer).save(str(output_dir))
        finalize_candidate(
            str(output_dir), reviewer, saved["candidate_digest"]
        )

        completes = [
            event for event in telemetry._read_events()
            if event["event"] == "agent_complete"
        ]
        assert [event["agent"] for event in completes] == [agent_name]

    def test_same_digest_finalization_is_idempotent(self, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = _start_telemetry(tmp_path, output_dir)
        _write_sidecar(output_dir)
        saved = _builder().save(str(output_dir))

        finalize_candidate(str(output_dir), "code", saved["candidate_digest"])
        retried = finalize_candidate(
            str(output_dir), "code", saved["candidate_digest"]
        )

        assert retried["already_finalized"] is True
        assert [
            event["event"] for event in telemetry._read_events()
        ].count("agent_complete") == 1

    def test_retry_repairs_completion_after_canonical_publication(
        self, tmp_path, monkeypatch
    ):
        import review.agent.output as output_mod

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = _start_telemetry(tmp_path, output_dir)
        _write_sidecar(output_dir)
        saved = _builder().save(str(output_dir))

        def _fail_once(*args, **kwargs):
            raise OSError("simulated telemetry append failure")

        monkeypatch.setattr(
            output_mod, "_log_agent_complete_telemetry", _fail_once
        )
        with pytest.raises(OSError, match="telemetry append"):
            finalize_candidate(
                str(output_dir), "code", saved["candidate_digest"]
            )

        assert (output_dir / "code-review.json").exists()
        assert not [
            event for event in telemetry._read_events()
            if event["event"] == "agent_complete"
        ]

        monkeypatch.undo()
        repaired = finalize_candidate(
            str(output_dir), "code", saved["candidate_digest"]
        )

        assert repaired["already_finalized"] is True
        assert [
            event["event"] for event in telemetry._read_events()
        ].count("agent_complete") == 1

"""Reviewer candidate publication and immutable finalization contracts."""

import contextlib
import hashlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import ReviewOutputBuilder, finalize_candidate
from review.reviewer_lifecycle import close_review_intake
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


def _edit_candidate(output_dir, edit):
    candidate = Path(output_dir, "code-review.candidate.json")
    payload = json.loads(candidate.read_text())
    edit(payload)
    edited = json.dumps(payload).encode()
    candidate.write_bytes(edited)
    return hashlib.sha256(edited).hexdigest()


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

    def test_boolean_review_schema_is_rejected_before_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path, lambda payload: payload.__setitem__("schema", True)
        )

        with pytest.raises(ValueError, match="schema"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        "required_field",
        [
            "pr_id",
            "reviewer",
            "timestamp",
            "plugin_version",
            "schema",
            "verdict",
            "summary",
            "issues",
            "unreviewed",
            "deferred_reviewed",
            "observations",
            "recommendations",
            "positive_observations",
            "clearances",
            "narrative_summary",
            "meta",
        ],
    )
    def test_missing_required_review_field_is_rejected_before_publication(
        self, tmp_path, required_field
    ):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path, lambda payload: payload.pop(required_field)
        )

        with pytest.raises(ValueError, match="review candidate"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        "required_field",
        [
            "id",
            "category",
            "severity",
            "title",
            "description",
            "file",
            "line",
            "recommendation",
            "confidence",
        ],
    )
    def test_missing_required_issue_field_is_rejected_before_publication(
        self, tmp_path, required_field
    ):
        _write_sidecar(tmp_path)
        _builder(issue_count=1).save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path,
            lambda payload: payload["issues"][0].pop(required_field),
        )

        with pytest.raises(ValueError, match="issue"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("pr_id", 42),
            ("timestamp", []),
            ("plugin_version", 114),
            ("narrative_summary", {}),
        ],
    )
    def test_wrong_review_field_type_is_rejected_before_publication(
        self, tmp_path, field, invalid_value
    ):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path,
            lambda payload: payload.__setitem__(field, invalid_value),
        )

        with pytest.raises(ValueError, match="review candidate"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("id", 1),
            ("category", []),
            ("title", {}),
            ("description", None),
            ("file", 1),
            ("recommendation", []),
            ("confidence", True),
            ("line", True),
        ],
    )
    def test_wrong_issue_field_type_is_rejected_before_publication(
        self, tmp_path, field, invalid_value
    ):
        _write_sidecar(tmp_path)
        _builder(issue_count=1).save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path,
            lambda payload: payload["issues"][0].__setitem__(
                field, invalid_value
            ),
        )

        with pytest.raises(ValueError, match="issue"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    def test_boolean_summary_count_is_rejected_before_publication(self, tmp_path):
        _write_sidecar(tmp_path)
        _builder(issue_count=1).save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path,
            lambda payload: payload["summary"]["by_severity"].__setitem__(
                "low", True
            ),
        )

        with pytest.raises(ValueError, match="summary"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        "required_field",
        [
            "review_duration_ms",
            "confidence_score",
            "tool_results_used",
        ],
    )
    def test_missing_required_meta_field_is_rejected_before_publication(
        self, tmp_path, required_field
    ):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        digest = _edit_candidate(
            tmp_path,
            lambda payload: payload["meta"].pop(required_field),
        )

        with pytest.raises(ValueError, match="meta"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize(
        ("container", "retired_key"),
        [
            ("meta", "unreviewed_" + "autofilled"),
            ("review", "declared_" + "unreviewed"),
            ("review", "files_declared_" + "unreviewed"),
            ("review", "files_autofilled_" + "unreviewed"),
        ],
    )
    def test_retired_coverage_key_is_rejected_before_publication(
        self, tmp_path, container, retired_key
    ):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))

        def _inject_retired_key(payload):
            target = payload["meta"] if container == "meta" else payload
            target[retired_key] = []

        digest = _edit_candidate(tmp_path, _inject_retired_key)

        with pytest.raises(ValueError, match="unexpected"):
            finalize_candidate(str(tmp_path), "code", digest)

        assert not (tmp_path / "code-review.json").exists()

    @pytest.mark.parametrize("skip_reason", [None, "", "   "])
    def test_not_applicable_requires_nonempty_skip_reason(
        self, tmp_path, skip_reason
    ):
        _write_sidecar(tmp_path)
        builder = _builder()
        builder.mark_not_applicable("No relevant changes")
        builder.save(str(tmp_path))

        def _replace_reason(payload):
            if skip_reason is None:
                payload.pop("skip_reason")
            else:
                payload["skip_reason"] = skip_reason

        digest = _edit_candidate(tmp_path, _replace_reason)

        with pytest.raises(ValueError, match="not_applicable"):
            finalize_candidate(str(tmp_path), "code", digest)

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
        close_review_intake(str(tmp_path), ["code-reviewer"])

        with pytest.raises(ValueError, match="intake"):
            _builder().save(str(tmp_path))

        assert not (tmp_path / "code-review.candidate.json").exists()
        assert not list(tmp_path.glob("code-review.candidate.json.*.tmp"))

    def test_closed_intake_rejects_finalization_without_losing_candidate(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)
        saved = _builder().save(str(tmp_path))
        close_review_intake(str(tmp_path), ["security-reviewer"])

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
        assert "RECORDED FINAL:" in finalized.stdout
        assert retried.returncode == 0
        assert "RECORDED FINAL (ALREADY FINALIZED):" in retried.stdout
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


class TestReviewIntakeClose:
    def test_close_discards_only_recognized_dispatched_candidates(
        self, tmp_path
    ):
        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        unrelated = tmp_path / "foreign-review.candidate.json"
        unrelated.write_text("{}")
        arbitrary = tmp_path / "notes.candidate.json"
        arbitrary.write_text("{}")

        closed = close_review_intake(
            str(tmp_path), ["code-reviewer", "security-reviewer"]
        )

        assert closed["schema"] == 1
        assert closed["status"] == "closed"
        assert closed["discarded_candidates"] == ["code-reviewer"]
        assert isinstance(closed["closed_at"], str)
        assert json.loads((tmp_path / "review-intake.json").read_text()) == closed
        assert not (tmp_path / "code-review.candidate.json").exists()
        assert unrelated.exists()
        assert arbitrary.exists()

    def test_repeated_close_unions_discards_and_finishes_interrupted_cleanup(
        self, tmp_path, monkeypatch
    ):
        import review.reviewer_lifecycle as lifecycle_mod

        _write_sidecar(tmp_path)
        _builder().save(str(tmp_path))
        original_unlink = lifecycle_mod.os.unlink
        failed = {"once": False}

        def fail_once(path):
            if path.endswith("code-review.candidate.json") and not failed["once"]:
                failed["once"] = True
                raise OSError("simulated interrupted cleanup")
            return original_unlink(path)

        monkeypatch.setattr(lifecycle_mod.os, "unlink", fail_once)
        with pytest.raises(OSError, match="interrupted cleanup"):
            close_review_intake(str(tmp_path), ["code-reviewer"])

        first = json.loads((tmp_path / "review-intake.json").read_text())
        assert first["discarded_candidates"] == ["code-reviewer"]
        assert (tmp_path / "code-review.candidate.json").exists()

        monkeypatch.undo()
        closed = close_review_intake(str(tmp_path), ["code-reviewer"])

        assert closed == first
        assert not (tmp_path / "code-review.candidate.json").exists()

    def test_close_preserves_canonical_and_repairs_missing_completion(
        self, tmp_path, monkeypatch
    ):
        import review.agent.output as output_mod

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = _start_telemetry(tmp_path, output_dir)
        _write_sidecar(output_dir)
        saved = _builder().save(str(output_dir))

        def fail_completion(*_args, **_kwargs):
            raise OSError("simulated completion append failure")

        monkeypatch.setattr(
            output_mod, "_log_agent_complete_telemetry", fail_completion
        )
        with pytest.raises(OSError, match="completion append"):
            finalize_candidate(
                str(output_dir), "code", saved["candidate_digest"]
            )
        canonical = output_dir / "code-review.json"
        canonical_bytes = canonical.read_bytes()
        assert not [
            event for event in telemetry._read_events()
            if event["event"] == "agent_complete"
        ]

        monkeypatch.undo()
        close_review_intake(str(output_dir), ["code-reviewer"])

        assert canonical.read_bytes() == canonical_bytes
        [completion] = [
            event for event in telemetry._read_events()
            if event["event"] == "agent_complete"
        ]
        assert completion["agent"] == "code-reviewer"
        assert completion["artifact_digest"] == saved["candidate_digest"]

        with pytest.raises(ValueError, match="intake"):
            finalize_candidate(
                str(output_dir), "code", saved["candidate_digest"]
            )

    def test_close_and_save_serialize_on_the_same_directory_lock(
        self, tmp_path, monkeypatch
    ):
        import review.agent.output as output_mod
        import review.reviewer_lifecycle as lifecycle_mod

        assert output_mod.output_dir_lock is lifecycle_mod.output_dir_lock
        _write_sidecar(tmp_path)
        mutex = threading.Lock()
        close_holds_lock = threading.Event()
        release_close = threading.Event()
        save_reached_lock = threading.Event()

        @contextlib.contextmanager
        def close_lock(_output_dir):
            with mutex:
                close_holds_lock.set()
                assert release_close.wait(timeout=5)
                yield

        @contextlib.contextmanager
        def save_lock(_output_dir):
            save_reached_lock.set()
            with mutex:
                yield

        monkeypatch.setattr(lifecycle_mod, "output_dir_lock", close_lock)
        monkeypatch.setattr(output_mod, "output_dir_lock", save_lock)

        with ThreadPoolExecutor(max_workers=2) as executor:
            close_future = executor.submit(
                close_review_intake, str(tmp_path), ["code-reviewer"]
            )
            assert close_holds_lock.wait(timeout=5)
            save_future = executor.submit(_builder().save, str(tmp_path))
            assert save_reached_lock.wait(timeout=5)
            assert not save_future.done()
            release_close.set()
            close_future.result(timeout=5)
            with pytest.raises(ValueError, match="intake"):
                save_future.result(timeout=5)

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

"""Canonical mutable-draft and immutable-final review lifecycle contracts."""

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

import review.agent.output as output_mod
import review.reviewer_lifecycle as lifecycle_mod
from review.agent.output import ReviewOutputBuilder, finalize_review
from review.reviewer_lifecycle import close_review_intake, review_paths
from review.telemetry import ReviewTelemetry


def _write_accounting_input(
    output_dir, reviewer="code", agent_name=None, claimable=None
):
    claimable = ["src/claimable.py"] if claimable is None else claimable
    Path(output_dir, f"{reviewer}-review-accounting-input.json").write_text(
        json.dumps({
            "schema": 3,
            "agent_name": agent_name or f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": claimable,
            "review_budget": 15,
            "inline_diff_file_count": len(claimable),
            "in_scope_review_file_count": len(claimable) + 1,
        })
    )


def _start_telemetry(tmp_path, output_dir):
    telemetry = ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
    telemetry.start(run_id="run-42")
    telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
    return telemetry


def _open_builder(output_dir, *, reviewer="code", pr_id="42"):
    return ReviewOutputBuilder.open(str(output_dir), pr_id, reviewer)


def _add_finding(builder, title="Finding"):
    return builder.add_finding(
        severity="low",
        title=title,
        file="src/code.py",
        line=1,
        description="Description",
        recommendation="Recommendation",
    )


class TestReviewPaths:
    def test_names_exactly_one_draft_final_and_accounting_input(self, tmp_path):
        paths = review_paths(str(tmp_path), "code")

        assert Path(paths.draft).name == "code-review.draft.json"
        assert Path(paths.final).name == "code-review.json"
        assert Path(paths.accounting_input).name == (
            "code-review-accounting-input.json"
        )


class TestDraftOpenAndReplacement:
    def test_first_open_binds_pathless_save_and_prints_compact_receipt(
        self, tmp_path, capsys
    ):
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)

        saved = builder.save_draft()

        output = capsys.readouterr().out
        assert set(saved) == {
            "draft", "review_digest", "finalize_review_command"
        }
        assert saved["draft"].endswith("code-review.draft.json")
        assert "DRAFT SAVED: verdict approve" in output
        assert "DRAFT TOTALS: findings 0" in output
        assert "FILES NOT YET CLAIMED AS REVIEWED (1): src/claimable.py" in output
        assert "FINALIZE REVIEW: " + saved["finalize_review_command"] in output
        assert "review_digest" not in output

    def test_rehydrates_every_builder_owned_field_and_preserves_finding_id(
        self, tmp_path
    ):
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)
        finding_id = _add_finding(builder)
        builder.add_observation("src/code.py", "Observed", "behavior")
        builder.add_recommendation("important", "Improve this")
        builder.add_positive_observation("Good boundary")
        builder.record_check("Safe path", "read exact caller", "No gap")
        builder.set_assessment("The implementation needs one fix.")
        builder.claim_files_reviewed("src/claimable.py")
        builder.set_confidence(0.81)
        builder.save_draft()

        reopened = _open_builder(tmp_path)

        assert reopened.timestamp == builder.timestamp
        assert [finding["id"] for finding in reopened.findings] == [finding_id]
        assert reopened.observations == builder.observations
        assert reopened.recommendations == builder.recommendations
        assert reopened.positive_observations == builder.positive_observations
        assert reopened.checks == builder.checks
        assert reopened.assessment == builder.assessment
        assert reopened.reviewed_file_claims == ["src/claimable.py"]
        assert reopened.overall_confidence == 0.81

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("pr_id", "99", "PR"),
            ("reviewer", "security", "reviewer"),
            ("schema", 999, "schema"),
        ],
    )
    def test_rejects_draft_bound_to_other_identity_or_schema(
        self, tmp_path, field, value, message
    ):
        _write_accounting_input(tmp_path)
        saved = _open_builder(tmp_path).save_draft()
        path = Path(saved["draft"])
        review = json.loads(path.read_text())
        review[field] = value
        path.write_text(json.dumps(review))

        with pytest.raises(ValueError, match=message):
            _open_builder(tmp_path)

    def test_rejects_malformed_draft(self, tmp_path):
        _write_accounting_input(tmp_path)
        Path(tmp_path, "code-review.draft.json").write_text("{not json")

        with pytest.raises(ValueError, match="malformed review draft"):
            _open_builder(tmp_path)

    def test_rejects_open_after_intake_close(self, tmp_path):
        _write_accounting_input(tmp_path)
        close_review_intake(str(tmp_path), ["code-reviewer"])

        with pytest.raises(ValueError, match="intake"):
            _open_builder(tmp_path)

    def test_rejects_open_after_finalization(self, tmp_path):
        _write_accounting_input(tmp_path)
        saved = _open_builder(tmp_path).save_draft()
        finalize_review(str(tmp_path), "code", saved["review_digest"])

        with pytest.raises(ValueError, match="finalized"):
            _open_builder(tmp_path)

    def test_absent_and_present_open_share_one_entrypoint(self, tmp_path):
        _write_accounting_input(tmp_path)
        first = _open_builder(tmp_path)
        finding_id = _add_finding(first)
        first.save_draft()

        present = _open_builder(tmp_path)

        assert present.pr_id == "42"
        assert present.findings[0]["id"] == finding_id

    def test_stale_builder_cannot_replace_newer_draft(self, tmp_path):
        _write_accounting_input(tmp_path)
        first = _open_builder(tmp_path)
        second = _open_builder(tmp_path)
        _add_finding(first, "First")
        first.save_draft()
        _add_finding(second, "Stale")

        with pytest.raises(ValueError, match="draft changed; reopen"):
            second.save_draft()

        assert json.loads(
            Path(tmp_path, "code-review.draft.json").read_text()
        )["findings"][0]["title"] == "First"

    def test_winning_builder_can_save_repeated_replacements(self, tmp_path):
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)
        first = builder.save_draft()
        _add_finding(builder)
        second = builder.save_draft()

        assert first["review_digest"] != second["review_digest"]
        assert json.loads(Path(second["draft"]).read_text())["summary"][
            "total_findings"
        ] == 1

    def test_atomic_replace_failure_leaves_no_draft_or_staging_file(
        self, tmp_path, monkeypatch
    ):
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)

        def _fail_replace(_source, _target):
            raise OSError("replace unavailable")

        monkeypatch.setattr(output_mod.os, "replace", _fail_replace)
        with pytest.raises(OSError, match="replace unavailable"):
            builder.save_draft()

        assert not Path(tmp_path, "code-review.draft.json").exists()
        assert not list(tmp_path.glob("code-review.draft.json.*.tmp"))


class TestFinalization:
    def test_digest_bound_finalization_is_idempotent(self, tmp_path):
        _write_accounting_input(tmp_path)
        saved = _open_builder(tmp_path).save_draft()

        first = finalize_review(str(tmp_path), "code", saved["review_digest"])
        retry = finalize_review(str(tmp_path), "code", saved["review_digest"])

        assert first["already_finalized"] is False
        assert retry["already_finalized"] is True
        assert Path(first["final"]).name == "code-review.json"
        assert not Path(saved["draft"]).exists()

    def test_old_digest_cannot_finalize_replacement(self, tmp_path):
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)
        old = builder.save_draft()
        _add_finding(builder)
        latest = builder.save_draft()

        with pytest.raises(ValueError, match="digest"):
            finalize_review(str(tmp_path), "code", old["review_digest"])

        assert Path(latest["draft"]).exists()
        assert not Path(tmp_path, "code-review.json").exists()

    def test_cli_first_and_retry_print_the_same_one_line(self, tmp_path):
        _write_accounting_input(tmp_path)
        saved = _open_builder(tmp_path).save_draft()
        command = [
            sys.executable,
            str(SCRIPTS_DIR / "review" / "agent" / "output.py"),
            "finalize-review",
            "--output-dir", str(tmp_path),
            "--reviewer", "code",
            "--review-digest", saved["review_digest"],
        ]

        first = subprocess.run(command, check=True, capture_output=True, text=True)
        retry = subprocess.run(command, check=True, capture_output=True, text=True)

        assert first.stdout == "REVIEW FINALIZED: code-review.json\n"
        assert retry.stdout == first.stdout
        assert first.stderr == retry.stderr == ""


class TestReviewIntakeClose:
    def test_close_discards_only_recognized_dispatched_drafts(self, tmp_path):
        _write_accounting_input(tmp_path)
        saved = _open_builder(tmp_path).save_draft()
        unrelated = Path(tmp_path, "foreign-review.draft.json")
        unrelated.write_text("{}")
        final = Path(tmp_path, "security-review.json")
        final.write_text("canonical")

        closed = close_review_intake(
            str(tmp_path), ["code-reviewer", "security-reviewer"]
        )

        assert closed["schema"] == 2
        assert closed["discarded_drafts"] == ["code-reviewer"]
        assert not Path(saved["draft"]).exists()
        assert unrelated.exists()
        assert final.read_text() == "canonical"

    def test_close_and_save_serialize_on_the_same_directory_lock(
        self, tmp_path, monkeypatch
    ):
        assert output_mod.output_dir_lock is lifecycle_mod.output_dir_lock
        _write_accounting_input(tmp_path)
        builder = _open_builder(tmp_path)
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
            save_future = executor.submit(builder.save_draft)
            assert save_reached_lock.wait(timeout=5)
            assert not save_future.done()
            release_close.set()
            close_future.result(timeout=5)
            with pytest.raises(ValueError, match="intake"):
                save_future.result(timeout=5)

        assert not Path(tmp_path, "code-review.draft.json").exists()


class TestFinalizationTelemetry:
    def test_two_draft_saves_and_one_finalization_have_split_semantics(
        self, tmp_path
    ):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = _start_telemetry(tmp_path, output_dir)
        _write_accounting_input(output_dir)
        builder = _open_builder(output_dir)
        builder.save_draft()
        _add_finding(builder)
        saved = builder.save_draft()

        finalize_review(str(output_dir), "code", saved["review_digest"])
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        events = telemetry._read_events()
        assert [event["event"] for event in events].count(
            "agent_review_draft_saved"
        ) == 2
        assert [event["event"] for event in events].count("agent_complete") == 1
        [complete] = [
            event for event in events if event["event"] == "agent_complete"
        ]
        assert complete["review_digest"] == saved["review_digest"]
        manifest = json.loads(Path(telemetry.manifest_path).read_text())
        [projected] = manifest["agents"]["completed"]
        assert projected["review_digest"] == saved["review_digest"]

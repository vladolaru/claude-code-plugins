"""Tests for review/critic.py — review-specific decision criticism pipeline."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "critic.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import critic as critic_module
from review import critic_adjustments as critic_adjustments_module
from review.critic_adjustments import read_critic_verdict


def run_critic(*args):
    """Run review/critic.py and return the result."""
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


class TestStepCount:
    """The script should have exactly 4 steps."""

    def test_total_steps_is_4(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        assert "Step 1/4" in result.stdout

    def test_total_steps_mismatch_is_invalid(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "7",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode != 0
        assert "must be 4" in result.stderr

    def test_step_5_is_invalid(self):
        result = run_critic(
            "--step-number", "5",
            "--total-steps", "4",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode != 0


class TestStepTitles:
    """Each step should have the expected phase and title."""

    @pytest.mark.parametrize("step,expected_title,expected_phase", [
        (1, "Decompose", "DECOMPOSITION"),
        (2, "Verify", "VERIFICATION"),
        (3, "Challenge", "CHALLENGE"),
        (4, "Synthesize", "SYNTHESIS"),
    ])
    def test_step_metadata(self, step, expected_title, expected_phase):
        result = run_critic(
            "--step-number", str(step),
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "accumulated state",
        )
        assert result.returncode == 0
        assert expected_title in result.stdout
        assert expected_phase in result.stdout


class TestNextStepDirective:
    """Each step except the last must direct to the next step."""

    @pytest.mark.parametrize("step", [1, 2, 3])
    def test_non_final_step_has_next(self, step):
        result = run_critic(
            "--step-number", str(step),
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert f"--step-number {step + 1}" in result.stdout

    def test_final_step_has_no_next(self):
        result = run_critic(
            "--step-number", "4",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "--step-number 5" not in result.stdout
        assert "COMPLETE" in result.stdout


class TestOutputPathInSynthesis:
    """Step 4 must include the output directory path."""

    def test_output_dir_in_step_4(self):
        result = run_critic(
            "--step-number", "4",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic-output",
            "--thoughts", "state",
        )
        assert "/tmp/test-critic-output" in result.stdout
        assert "decision-critic-findings.md" in result.stdout


class TestCriticContextArg:
    """`--context` carries the structured findings ledger.

    It used to point at `critic-context.md`, a Markdown document built per
    run purely to merge the report and the ledger for this one agent. The
    record replaced the merge, so the flag now names `review-findings.json`
    directly — the file whose ids the critic's adjustments have to resolve
    against.
    """

    def test_context_surfaced_in_step_1(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/review-record.md",
            "--context", "/tmp/review-findings.json",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        assert "review-findings.json" in result.stdout

    def test_context_surfaced_in_step_2(self):
        """Step 2 should also reference the context path."""
        result = run_critic(
            "--step-number", "2",
            "--total-steps", "4",
            "--report", "/tmp/review-record.md",
            "--context", "/tmp/review-findings.json",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert result.returncode == 0
        assert "review-findings.json" in result.stdout

    def test_step_1_keys_claims_to_the_ledger_ids(self):
        """A positional label is a rendering artifact no ledger contains;
        an adjustment keyed by one resolves to nothing."""
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/review-record.md",
            "--context", "/tmp/review-findings.json",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        assert "8-hex" in result.stdout
        assert "issues[].id" in result.stdout


class TestCriticSave:
    """--save is the ONLY channel the decision-reviewer agent is allowed to
    write decision-critic-* artifacts through (agents/decision-reviewer.md):
    validation preserves the previous snapshot, while publication removes
    the old verdict marker before replacing either payload and commits the
    new snapshot by writing its verdict last."""

    def _run_save(self, output_dir, *extra_args):
        cmd = [
            sys.executable, str(SCRIPT), "--save",
            "--output-dir", str(output_dir), *extra_args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def _write_findings(self, tmp_path, text="# Decision Critic Findings\n"):
        path = tmp_path / "f.md"
        path.write_text(text)
        return path

    def _write_adjustments(self, tmp_path, adjustments):
        path = tmp_path / "a.json"
        path.write_text(json.dumps({"schema": 1, "adjustments": adjustments}))
        return path

    @staticmethod
    def _args(tmp_path, verdict, findings, adjustments=None):
        return SimpleNamespace(
            output_dir=str(tmp_path), verdict=verdict,
            findings=str(findings),
            adjustments=str(adjustments) if adjustments else None,
        )

    @staticmethod
    def _write_complete_snapshot(tmp_path, verdict="REVISE"):
        paths = {
            "findings": tmp_path / "decision-critic-findings.md",
            "adjustments": tmp_path / "decision-critic-adjustments.json",
            "verdict": tmp_path / "decision-critic-verdict.json",
        }
        paths["findings"].write_text("# Previous complete findings\n")
        entries = [{
            "adjustment_id": "previous-decision",
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "previous",
        }] if verdict == "REVISE" else []
        paths["adjustments"].write_text(json.dumps({
            "schema": 1, "adjustments": entries,
        }))
        proposal = json.loads(paths["adjustments"].read_text())
        paths["verdict"].write_text(json.dumps({
            "schema": 1,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                proposal
            ),
        }))
        return paths

    def test_findings_write_failure_invalidates_previous_commit_marker(
        self, tmp_path, monkeypatch
    ):
        paths = self._write_complete_snapshot(tmp_path)
        findings = self._write_findings(tmp_path, "# Replacement findings\n")

        def fail_findings_write(_path, _text):
            raise OSError("injected findings write failure")

        monkeypatch.setattr(
            critic_module, "atomic_write_text", fail_findings_write
        )

        with pytest.raises(OSError, match="injected findings write failure"):
            critic_module.run_save(
                self._args(tmp_path, "STAND", findings)
            )

        assert not paths["verdict"].exists()
        assert read_critic_verdict(str(tmp_path)) is None
        assert paths["findings"].read_text() == "# Previous complete findings\n"

    def test_adjustment_write_failure_leaves_no_committed_mixed_snapshot(
        self, tmp_path, monkeypatch
    ):
        paths = self._write_complete_snapshot(tmp_path, verdict="STAND")
        findings = self._write_findings(tmp_path, "# Replacement findings\n")
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "replacement",
        }])
        real_write_adjustments = critic_adjustments_module.write_adjustments

        def fail_adjustment_write(_output_dir, _payload):
            raise OSError("injected adjustment write failure")

        monkeypatch.setattr(
            critic_adjustments_module, "write_adjustments", fail_adjustment_write
        )

        with pytest.raises(OSError, match="injected adjustment write failure"):
            critic_module.run_save(
                self._args(tmp_path, "REVISE", findings, adjustments)
            )

        assert paths["findings"].read_text() == "# Replacement findings\n"
        assert json.loads(paths["adjustments"].read_text()) == {
            "schema": 1, "adjustments": [],
        }
        assert not paths["verdict"].exists()
        assert read_critic_verdict(str(tmp_path)) is None

    def test_commit_marker_invalidation_error_fails_before_payload_writes(
        self, tmp_path, monkeypatch
    ):
        paths = self._write_complete_snapshot(tmp_path)
        before = {name: path.read_bytes() for name, path in paths.items()}
        findings = self._write_findings(tmp_path, "# Replacement findings\n")

        def fail_unlink(_path):
            raise PermissionError("injected marker invalidation failure")

        monkeypatch.setattr(critic_module.os, "unlink", fail_unlink)

        with pytest.raises(
            PermissionError, match="injected marker invalidation failure"
        ):
            critic_module.run_save(
                self._args(tmp_path, "STAND", findings)
            )

        assert {
            name: path.read_bytes() for name, path in paths.items()
        } == before

    def test_validation_rejection_preserves_previous_complete_snapshot(
        self, tmp_path
    ):
        paths = self._write_complete_snapshot(tmp_path)
        before = {name: path.read_bytes() for name, path in paths.items()}
        findings = self._write_findings(tmp_path, "# Rejected replacement\n")

        result = critic_module.run_save(
            self._args(tmp_path, "MAYBE", findings)
        )

        assert result == 1
        assert {
            name: path.read_bytes() for name, path in paths.items()
        } == before

    def test_critic_save_writes_a_complete_snapshot(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "decision-critic-findings.md").is_file()
        assert (tmp_path / "decision-critic-adjustments.json").is_file()
        verdict_doc = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert verdict_doc == {
            "schema": 1,
            "verdict": "REVISE",
            "proposal_digest": critic_adjustments_module.proposal_digest(
                proposal
            ),
        }

    def test_critic_save_rejects_bad_verdict(self, tmp_path):
        findings = self._write_findings(tmp_path)

        result = self._run_save(
            tmp_path, "--verdict", "MAYBE", "--findings", str(findings),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert [p.name for p in tmp_path.iterdir()] == [findings.name], (
            "a rejected save must write nothing"
        )

    def test_critic_save_rejects_revise_without_adjustments(self, tmp_path):
        findings = self._write_findings(tmp_path)

        result = self._run_save(
            tmp_path, "--verdict", "REVISE", "--findings", str(findings),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "adjustments" in result.stdout.lower()
        assert [p.name for p in tmp_path.iterdir()] == [findings.name]

    @pytest.mark.parametrize("verdict", ["STAND", "ESCALATE"])
    def test_critic_save_rejects_non_revise_with_adjustments(
        self, tmp_path, verdict
    ):
        """STAND/ESCALATE alongside a non-empty batch is the contradiction
        the apply gate could only quarantine downstream; now rejected at
        source. run_save() treats both verdicts identically — pin both,
        not just one."""
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", verdict,
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "contradiction" in result.stdout.lower()
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ], "a rejected save must write nothing"

    @pytest.mark.parametrize("verdict", ["STAND", "ESCALATE"])
    def test_critic_save_without_adjustments_replaces_stale_snapshot(
        self, tmp_path, verdict
    ):
        """A successful verdict is the current snapshot, so a pending
        REVISE batch from an earlier attempt may not survive it."""
        findings = self._write_findings(tmp_path)
        snapshot = tmp_path / "decision-critic-adjustments.json"
        snapshot.write_text(json.dumps({
            "schema": 1,
            "adjustments": [{
                "adjustment_id": "stale-revise",
                "action": "promote",
                "id": "aaaa1111",
                "fields": {"severity": "high"},
                "rationale": "pending from an earlier attempt",
            }],
        }))

        result = self._run_save(
            tmp_path, "--verdict", verdict, "--findings", str(findings),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "decision-critic-findings.md").is_file()
        assert json.loads(snapshot.read_text()) == {
            "schema": 1, "adjustments": [],
        }
        verdict_doc = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        assert verdict_doc == {
            "schema": 1,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                {"schema": 1, "adjustments": []}
            ),
        }
        assert f"RECORDED VERDICT: {verdict}" in result.stdout
        assert "RECORDED ADJUSTMENTS: 0" in result.stdout

    def test_critic_save_rejects_two_simultaneous_problems(self, tmp_path):
        """run_save() must collect every problem, not stop at the first —
        the same "don't stop at problems[0]" contract TestValidateAdjustments
        pins for the validator itself, pinned here at the save-command
        level with a bad verdict AND an invalid batch in the same call."""
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "MAYBE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        rejected_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("REJECTED:")
        ]
        assert len(rejected_lines) == 2, (
            f"expected exactly 2 REJECTED lines, got: {rejected_lines}"
        )
        assert any("verdict must be one of" in line for line in rejected_lines)
        assert any("obliterate" in line for line in rejected_lines)
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ], "a rejected save must write nothing"

    def test_critic_save_rejects_invalid_batch(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "obliterate" in result.stdout
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ]

    def test_critic_save_echo_names_what_was_recorded(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "REVISE" in result.stdout
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert proposal["adjustments"][0]["adjustment_id"] in result.stdout
        assert "?" not in result.stdout


class TestSourceBoundCriticSave:
    """The critic owns proposal facts only; the save channel owns identity
    allocation and commits the exact proposal with a digest-bound marker."""

    def _run_save(self, output_dir, verdict, findings, adjustments=None):
        args = [
            sys.executable,
            str(SCRIPT),
            "--save",
            "--output-dir",
            str(output_dir),
            "--verdict",
            verdict,
            "--findings",
            str(findings),
        ]
        if adjustments is not None:
            args.extend(["--adjustments", str(adjustments)])
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    @staticmethod
    def _write_findings(tmp_path, text="# Decision Critic Findings\n"):
        path = tmp_path / "critic-source.md"
        path.write_text(text)
        return path

    @staticmethod
    def _write_payload(tmp_path, payload):
        path = tmp_path / "critic-proposal.json"
        path.write_text(json.dumps(payload))
        return path

    def test_save_assigns_ids_and_commits_the_proposal_digest(self, tmp_path):
        findings = self._write_findings(tmp_path)
        proposal_input = self._write_payload(tmp_path, {
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "aaaa1111",
                "fields": {"severity": "medium"},
                "rationale": "The claimed impact is narrower than stated.",
            }],
        })

        result = self._run_save(
            tmp_path, "REVISE", findings, proposal_input
        )

        assert result.returncode == 0, result.stdout + result.stderr
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        marker = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        adjustment_id = proposal["adjustments"][0]["adjustment_id"]
        assert adjustment_id
        assert marker == {
            "schema": 1,
            "verdict": "REVISE",
            "proposal_digest": critic_adjustments_module.proposal_digest(
                proposal
            ),
        }
        assert adjustment_id in result.stdout
        assert marker["proposal_digest"] in result.stdout
        assert "?" not in result.stdout

    @pytest.mark.parametrize("verdict", ["STAND", "ESCALATE"])
    def test_non_revise_marker_commits_the_canonical_empty_proposal(
        self, tmp_path, verdict
    ):
        findings = self._write_findings(tmp_path)

        result = self._run_save(tmp_path, verdict, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        marker = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        assert proposal == {"schema": 1, "adjustments": []}
        assert marker == {
            "schema": 1,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                proposal
            ),
        }

    @pytest.mark.parametrize(
        "forbidden,value",
        [
            ("adjustment_id", "critic-chosen"),
            ("spot_check", "verified"),
            ("rejected", True),
            ("rejection_reason", "caller-authored"),
            ("applied", True),
        ],
    )
    def test_save_rejects_every_caller_authored_lifecycle_field(
        self, tmp_path, forbidden, value
    ):
        findings = self._write_findings(tmp_path)
        entry = {
            "action": "demote",
            "id": "aaaa1111",
            "fields": {"severity": "medium"},
            "rationale": "The claimed impact is narrower than stated.",
            forbidden: value,
        }
        proposal_input = self._write_payload(tmp_path, {
            "schema": 1,
            "adjustments": [entry],
        })
        old_findings = tmp_path / "decision-critic-findings.md"
        old_adjustments = tmp_path / "decision-critic-adjustments.json"
        old_marker = tmp_path / "decision-critic-verdict.json"
        old_findings.write_text("old findings")
        old_adjustments.write_text('{"old": "proposal"}')
        old_marker.write_text('{"old": "marker"}')
        before = {
            path: path.read_bytes()
            for path in (old_findings, old_adjustments, old_marker)
        }

        result = self._run_save(
            tmp_path, "REVISE", findings, proposal_input
        )

        assert result.returncode == 1
        assert "REJECTED:" in result.stdout
        assert forbidden in result.stdout
        assert {path: path.read_bytes() for path in before} == before

    @pytest.mark.parametrize(
        "top_level",
        [
            {"revised_narrative": "critic-authored"},
            {"adjudication": {"source": "critic"}},
        ],
    )
    def test_save_rejects_caller_authored_settlement_document_fields(
        self, tmp_path, top_level
    ):
        findings = self._write_findings(tmp_path)
        payload = {
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "aaaa1111",
                "fields": {"severity": "medium"},
                "rationale": "Narrower than stated.",
            }],
            **top_level,
        }
        proposal_input = self._write_payload(tmp_path, payload)

        result = self._run_save(
            tmp_path, "REVISE", findings, proposal_input
        )

        assert result.returncode == 1
        assert "REJECTED:" in result.stdout
        assert next(iter(top_level)) in result.stdout

    def test_retry_after_publication_fault_produces_one_coherent_snapshot(
        self, tmp_path, monkeypatch
    ):
        findings = self._write_findings(tmp_path)
        proposal_input = self._write_payload(tmp_path, {
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "aaaa1111",
                "fields": {"severity": "medium"},
                "rationale": "Narrower than stated.",
            }],
        })
        old_marker = tmp_path / "decision-critic-verdict.json"
        old_marker.write_text('{"old": "marker"}')
        real_write = critic_adjustments_module.write_adjustments
        calls = 0

        def fail_once(output_dir, document):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected proposal publication failure")
            return real_write(output_dir, document)

        monkeypatch.setattr(
            critic_adjustments_module, "write_adjustments", fail_once
        )
        args = SimpleNamespace(
            output_dir=str(tmp_path),
            verdict="REVISE",
            findings=str(findings),
            adjustments=str(proposal_input),
        )

        with pytest.raises(OSError, match="injected proposal"):
            critic_module.run_save(args)
        assert not old_marker.exists()

        assert critic_module.run_save(args) == 0
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        marker = json.loads(old_marker.read_text())
        assert marker["proposal_digest"] == (
            critic_adjustments_module.proposal_digest(proposal)
        )


class TestReviewSpecificLanguage:
    """Prompts should contain review-specific terms, not generic decision language."""

    def test_step_2_mentions_source_code(self):
        result = run_critic(
            "--step-number", "2",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "source" in result.stdout.lower() or "code" in result.stdout.lower()
        assert "file" in result.stdout.lower()

    def test_step_3_mentions_severity(self):
        result = run_critic(
            "--step-number", "3",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "severity" in result.stdout.lower() or "false positive" in result.stdout.lower()

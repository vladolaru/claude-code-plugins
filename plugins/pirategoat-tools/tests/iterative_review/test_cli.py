"""Tests for iterative_review CLI -- argument parsing and action routing."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR_PARENT = Path(__file__).resolve().parent.parent  # tests/ dir
PLUGIN_ROOT_FOR_IMPORTS = TESTS_DIR_PARENT.parent
SCRIPTS_DIR_FOR_IMPORTS = PLUGIN_ROOT_FOR_IMPORTS / "scripts"
if str(SCRIPTS_DIR_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR_FOR_IMPORTS))
from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
from iterative_review.paths import iterative_artifact_path, round_artifact_path

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
MODULE_DIR = SCRIPTS_DIR / "iterative_review"

sys.path.insert(0, str(SCRIPTS_DIR))
import iterative_review.__main__ as main_mod


def _artifact(output_dir, key):
    path = iterative_artifact_path(output_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _round_artifact(output_dir, round_num, key):
    path = round_artifact_path(output_dir, round_num, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class TestCLIParsing:
    def test_review_action_requires_merge_base_on_round_1(self, tmp_path, monkeypatch, capsys):
        """Round 1 requires --merge-base."""
        monkeypatch.setattr(sys, "argv", [
            "__main__.py", "--action", "review", "--round", "1",
            "--output-dir", str(tmp_path / "code-review"),
        ])
        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code != 0
        stderr = capsys.readouterr().err.lower()
        assert "merge-base" in stderr or "required" in stderr

    def test_review_round2_rejects_missing_state(self, tmp_path, monkeypatch, capsys):
        """Round 2+ fails fast when no persisted state exists."""
        d = tmp_path / "code-review"
        d.mkdir()
        monkeypatch.setattr(sys, "argv", [
            "__main__.py", "--action", "review", "--round", "2",
            "--output-dir", str(d),
        ])
        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code != 0
        assert "round 1 must run first" in capsys.readouterr().err.lower()

    def test_advance_action_requires_output_dir(self, monkeypatch, capsys):
        """Advance requires --output-dir."""
        monkeypatch.setattr(sys, "argv", [
            "__main__.py", "--action", "advance", "--round", "1",
        ])
        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code != 0

    def test_advance_rejects_missing_outcomes(self, tmp_path):
        """Advance fails if outcomes file doesn't exist."""
        d = tmp_path / "code-review"
        d.mkdir()
        # Write state but no outcomes
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "terminated": False}
        _artifact(d, "state").write_text(json.dumps(state))
        # Write findings so advance expects outcomes
        _round_artifact(d, 1, "findings").write_text(json.dumps([
            {"id": "r1_f1", "severity": "P1", "title": "Test", "body": "X", "location": "a.py:1"}
        ]))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0

class TestDeferredPruning:
    """Deferred items resolved in later rounds are pruned from result."""

    def test_resolved_deferred_pruned_from_result(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()

        # Round 1: one fixed (keeps loop going), one deferred.
        # Use max_rounds=3 so round 2 isn't at the limit (avoids P2 extension).
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        r1_findings = [
            {"id": "r1_f1", "severity": "P2", "title": "Typo", "body": "X", "location": "readme.md:1"},
            {"id": "r1_f2", "severity": "P2", "title": "Null check", "body": "X", "location": "handler.py:42"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(r1_findings))
        r1_outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Fixed typo."},
            {"id": "r1_f2", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(r1_outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
        )

        # Round 2: same deferred issue found again and fixed.
        # P3 triggers nitpicks_only convergence so the loop terminates here.
        r2_findings = [{"id": "r2_f1", "severity": "P3", "title": "Null check",
                        "body": "X", "location": "handler.py:42"}]
        _round_artifact(d, 2, "findings").write_text(json.dumps(r2_findings))
        r2_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Added null check."}]
        _round_artifact(d, 2, "outcomes").write_text(json.dumps(r2_outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "2",
             "--output-dir", str(d)],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
        )

        # The result should prune the deferred item (same title+location was fixed)
        result_path = _artifact(d, "result")
        assert result_path.exists()
        result = json.loads(result_path.read_text())
        assert len(result.get("deferred_items", [])) == 0


class TestAdvanceIdempotency:
    """Advance is idempotent — retrying the same round doesn't duplicate records."""

    def test_retry_does_not_duplicate_round(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"}]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "fixed", "summary": "Done."}]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        cmd = [sys.executable, "-m", "iterative_review",
               "--action", "advance", "--round", "1",
               "--output-dir", str(d)]

        # Run advance twice
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))

        updated_state = json.loads(_artifact(d, "state").read_text())
        round_records = [r for r in updated_state["rounds"] if r["round"] == 1]
        assert len(round_records) == 1, f"Expected 1 record for round 1, got {len(round_records)}"


class TestAdvanceRoundSummary:
    """Advance action correctly records round summary in state."""

    def test_round_summary_counts(self, tmp_path):
        """Round summary records correct fixed/rejected/deferred counts."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P1", "title": "B", "body": "Y", "location": "b.py:2"},
            {"id": "r1_f3", "severity": "P2", "title": "C", "body": "Z", "location": "c.py:3"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Fixed it."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
            {"id": "r1_f3", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert len(updated_state["rounds"]) == 1
        r = updated_state["rounds"][0]
        assert r["fixed"] == 1
        assert r["rejected"] == 1
        assert r["deferred"] == 1
        assert r["findings"] == 3

    def test_deferred_items_written(self, tmp_path):
        """Deferred findings are written to deferred-items.jsonl."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        jsonl_path = _artifact(d, "deferred")
        assert jsonl_path.exists()
        items = [json.loads(line) for line in jsonl_path.read_text().strip().split("\n")]
        assert len(items) == 1
        assert items[0]["id"] == "r1_f1"


def _advance(tmp_path, round_num, max_rounds, findings_and_actions):
    """Write state/findings/outcomes for one round and run `--action advance`
    for real (the round-extension arithmetic is inline in `__main__.py`,
    not an extractable function, so the CLI is the only level it exists
    at). `findings_and_actions` is a list of (severity, action) pairs, one
    per finding. Returns (subprocess result, updated state dict)."""
    d = tmp_path / "code-review"
    d.mkdir()
    state = {"current_round": round_num, "max_rounds": max_rounds, "rounds": [],
             "merge_base": "abc", "diff_lines_relevant": 100,
             "terminated": False, "termination": None,
             "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
    _artifact(d, "state").write_text(json.dumps(state))
    findings = []
    outcomes = []
    for i, (severity, action) in enumerate(findings_and_actions, 1):
        fid = f"r{round_num}_f{i}"
        findings.append({"id": fid, "severity": severity, "title": "T",
                          "body": "X", "location": "a.py:1"})
        if action == "fixed":
            outcomes.append({"id": fid, "action": "fixed", "summary": "Fixed."})
        else:
            outcomes.append({"id": fid, "action": action, "reasoning": "Out of scope."})
    _round_artifact(d, round_num, "findings").write_text(json.dumps(findings))
    _round_artifact(d, round_num, "outcomes").write_text(json.dumps(outcomes))

    result = subprocess.run(
        [sys.executable, "-m", "iterative_review",
         "--action", "advance", "--round", str(round_num),
         "--output-dir", str(d)],
        capture_output=True, text=True,
        cwd=str(SCRIPTS_DIR),
    )
    updated_state = json.loads(_artifact(d, "state").read_text())
    return result, updated_state


class TestAdvanceConvergence:
    """Advance action detects convergence and extends the round budget by
    finding severity, capped at the hard limit. P0 and P1 are the same
    extension branch (`s in ("P0", "P1")` → +2), so only P1 has a row."""

    @pytest.mark.parametrize(
        "round_num,max_rounds,findings_and_actions,expect_terminated,expect_max_rounds,expect_termination",
        [
            pytest.param(
                3, 3, [("P3", "fixed"), ("P2", "rejected")],
                True, 3, "max_rounds", id="p3-fixed-terminates",
            ),
            pytest.param(3, 3, [("P1", "fixed")], False, 5, None, id="p1-fixed-extends-by-2"),
            pytest.param(3, 3, [("P2", "fixed")], False, 4, None, id="p2-fixed-extends-by-1"),
            pytest.param(
                3, 3, [("P1", "deferred")], True, 3, None, id="p1-deferred-does-not-extend",
            ),
            pytest.param(
                15, 15, [("P1", "fixed")], True, 15, "hard_limit", id="p1-at-hard-limit",
            ),
            pytest.param(
                MAX_ROUNDS_HARD_LIMIT - 1, MAX_ROUNDS_HARD_LIMIT - 1, [("P0", "fixed")],
                False, MAX_ROUNDS_HARD_LIMIT, None, id="p0-extension-caps-at-hard-limit",
            ),
        ],
    )
    def test_advance_extension(self, tmp_path, round_num, max_rounds, findings_and_actions,
                                expect_terminated, expect_max_rounds, expect_termination):
        result, updated_state = _advance(tmp_path, round_num, max_rounds, findings_and_actions)
        assert result.returncode == 0
        assert updated_state["terminated"] is expect_terminated
        assert updated_state["max_rounds"] == expect_max_rounds
        if expect_termination is not None:
            assert updated_state["termination"] == expect_termination
        if not expect_terminated:
            assert f"round {round_num + 1}" in result.stdout.lower()


class TestAdvanceResultFile:
    """Advance writes review-loop-result.json on termination."""

    def test_result_file_written_on_termination(self, tmp_path):
        """review-loop-result.json is written when loop terminates."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "T", "body": "B", "location": "a.py:1"}]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "False positive."}]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = _artifact(d, "result")
        assert result_path.exists()
        result_data = json.loads(result_path.read_text())
        assert result_data["termination"] == "all_rejected"
        assert result_data["rounds_completed"] == 1
        assert result_data["total_rejected"] == 1
        assert result_data["total_fixed"] == 0

    def test_no_result_file_when_continuing(self, tmp_path):
        """review-loop-result.json is NOT written when loop continues."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = _artifact(d, "result")
        assert not result_path.exists()


class TestAdvanceTerminatedState:
    """Advance action handles already-terminated state."""

    def test_advance_on_terminated_state_prints_completion(self, tmp_path):
        """Advance on already-terminated state prints completion briefing."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 2, "max_rounds": 3,
                 "rounds": [{"round": 1, "findings": 2, "fixed": 1, "rejected": 1, "deferred": 0}],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": True, "termination": "all_rejected",
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "2",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        assert "complete" in result.stdout.lower()


class TestAdvanceMissingOutcomes:
    """Advance validates outcome completeness."""

    def test_advance_rejects_incomplete_outcomes(self, tmp_path):
        """Advance fails if not all findings have outcomes."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        # Only outcome for r1_f1 -- r1_f2 is missing
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0
        assert "r1_f2" in result.stderr


class TestSchemaFile:
    """The review-schema.json file must exist for Codex invocation."""

    def test_schema_is_valid_json(self):
        schema_path = SCRIPTS_DIR / "iterative_review" / "backends" / "codex-review-schema.json"
        assert schema_path.exists(), f"Missing {schema_path}"
        data = json.loads(schema_path.read_text())
        assert "properties" in data
        assert "findings" in data["properties"]
        assert data.get("additionalProperties") is False

    def test_get_schema_path_returns_existing_file(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        from iterative_review.backends.codex import get_schema_path
        path = get_schema_path()
        assert Path(path).exists(), f"get_schema_path() returned {path} but file doesn't exist"


# ---------------------------------------------------------------------------
# Pre-flight check — backend selection, availability and auth
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPTS_DIR))
from iterative_review.__main__ import _preflight_backend


class TestPreflightBackend:
    """_preflight_backend selects best backend and verifies availability."""

    @patch("iterative_review.backends.codex.check_auth", return_value=(True, ""))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_returns_module_when_codex_available_and_authed(self, mock_which, mock_auth):
        backend, name, err = _preflight_backend()
        assert err is None
        assert name == "codex"
        assert backend is not None
        # Common interface: module has callable check_auth and invoke_review
        assert callable(backend.check_auth)
        assert callable(backend.invoke_review)

    @patch("shutil.which", return_value=None)
    def test_returns_error_when_nothing_installed(self, mock_which):
        backend, name, err = _preflight_backend()
        assert backend is None
        assert err is not None
        assert "UNAVAILABLE" in err
        assert "not installed" in err or "not on PATH" in err

    @patch("iterative_review.backends.codex.check_auth", return_value=(False, "not logged in"))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_returns_error_when_codex_not_authenticated(self, mock_which, mock_auth):
        """Codex on PATH but not authenticated, Claude not on PATH -> error."""
        # shutil.which returns truthy for any arg, but _select_backend
        # calls check_auth which fails, then tries claude which also
        # gets truthy which but we need to make it fail too.
        # With mock returning "/usr/local/bin/codex" for all calls,
        # _select_backend tries codex auth (fails), then claude auth.
        # We need claude auth to also fail for this test.
        with patch("iterative_review.backends.claude.check_auth",
                    return_value=(False, "not authenticated")):
            backend, name, err = _preflight_backend()
            assert backend is None
            assert err is not None
            assert "UNAVAILABLE" in err

    @patch("iterative_review.backends.claude.check_auth", return_value=(True, "v2.0"))
    @patch("iterative_review.backends.codex.check_auth", return_value=(False, "not logged in"))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_falls_back_to_claude_when_codex_unauthed(self, mock_which, mock_codex_auth, mock_claude_auth):
        """Codex not authenticated -> falls back to Claude Code."""
        backend, name, err = _preflight_backend()
        assert err is None
        assert name == "claude"
        assert backend is not None
        assert callable(backend.check_auth)


class TestTryFallback:
    """_try_fallback selects the other backend after a runtime failure."""

    @patch("iterative_review.backends.claude.check_auth", return_value=(True, '{"loggedIn": true}'))
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_codex_failure_falls_back_to_claude(self, mock_which, mock_auth):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("codex")
        assert name == "claude"
        assert backend is not None

    @patch("iterative_review.backends.codex.check_auth", return_value=(True, ""))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_claude_failure_falls_back_to_codex(self, mock_which, mock_auth):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("claude")
        assert name == "codex"
        assert backend is not None

    @patch("shutil.which", return_value=None)
    def test_no_fallback_when_other_not_available(self, mock_which):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("codex")
        assert backend is None
        assert name is None


class TestAdaptiveEffortFlag:
    """--adaptive-effort is accepted by the parser (top-level, not scoped
    to one --action) and threads through to the dispatched action's args."""

    def test_flag_parses_true_and_reaches_action_review(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(main_mod, "action_review", lambda args: captured.setdefault("args", args))
        monkeypatch.setattr(sys, "argv", [
            "__main__.py", "--action", "review", "--round", "1",
            "--output-dir", str(tmp_path / "code-review"),
            "--merge-base", "abc123", "--adaptive-effort",
        ])
        main_mod.main()
        assert captured["args"].adaptive_effort is True


class TestPreflightIntegration:
    """Pre-flight failure writes result file and exits cleanly."""

    def test_unavailable_writes_result_and_exits_zero(self, tmp_path):
        """When codex is not on PATH, script writes result file and exits 0."""
        d = tmp_path / "review-output"
        d.mkdir()
        # Write a helper script that patches shutil.which before importing
        helper = tmp_path / "run_preflight.py"
        helper.write_text(
            f"import sys\n"
            f"sys.path.insert(0, '{SCRIPTS_DIR}')\n"
            f"from unittest.mock import patch\n"
            f"import types\n"
            f"args = types.SimpleNamespace(\n"
            f"    output_dir='{d}',\n"
            f"    round=1, merge_base='abc123', context_file=None,\n"
            f"    max_rounds=None, no_prior_analysis=False)\n"
            f"with patch('shutil.which', return_value=None):\n"
            f"    from iterative_review.__main__ import action_review\n"
            f"    action_review(args)\n"
        )
        result = subprocess.run(
            [sys.executable, str(helper)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        assert "UNAVAILABLE" in result.stdout
        # Verify result file was written
        result_path = _artifact(d, "result")
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["termination"] == "backend_unavailable"
        assert data["rounds_completed"] == 0


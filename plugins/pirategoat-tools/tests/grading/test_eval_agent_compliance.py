"""
Tests for the eval_agent_compliance CLI — the documented offline
compliance-grading entry point.

test_graders.py covers the grading library; these cover the runner that
wraps it. The runner is a subprocess CLI, so it is exercised as one —
its module-level imports and argument wiring are exactly what unit tests
of the library can't reach (see TESTING.md §"subprocess only for
orchestration").
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
EVAL_SCRIPT = TESTS_DIR / "grading" / "eval_agent_compliance.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from review.agent.output import ReviewOutputBuilder, finalize_review

# Same precedent as grading/test_graders.py: add tests/ to sys.path so the
# `helpers` package resolves, then import graders directly from their real
# source rather than through the eval module's re-export.
sys.path.insert(0, str(TESTS_DIR))
from helpers.graders import GradeResult, grade_review_markdown
from helpers.review_fixtures import canonical_assignment

# The runner is mostly exercised as a subprocess (see _run_eval), but
# run_grade_only is also called directly to inspect its GradeResults.
# Load it the same way the runner itself loads bootstrap: by exact path.
_eval_spec = importlib.util.spec_from_file_location(
    "_eval_agent_compliance_under_test", str(EVAL_SCRIPT),
)
_eval_mod = importlib.util.module_from_spec(_eval_spec)
_eval_spec.loader.exec_module(_eval_mod)
run_grade_only = _eval_mod.run_grade_only


def _write_review_pair(output_dir: Path, reviewer: str = "security") -> None:
    """Produce a real review output pair with the production builder."""
    builder = ReviewOutputBuilder.open(output_dir, "1", reviewer)
    builder.add_finding(
        severity="high",
        title="Unescaped output",
        file="src/render.php",
        description="Value is echoed without escaping",
        recommendation="Wrap in esc_html()",
        category="xss",
        line=42,
    )
    builder.record_check(
        "Does any caller escape the value before rendered output?",
        "Enumerate every caller and trace each path to the rendering sink",
        "No caller escapes the value before it reaches the sink.",
    )
    (output_dir / f"{reviewer}-assignment.json").write_text(json.dumps(
        canonical_assignment(reviewer, inline_diff_file_count=1)
    ))
    saved = builder.save_draft()
    finalize_review(
        str(output_dir), reviewer, saved["review_digest"]
    )


def _run_eval(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke the runner from an isolated cwd.

    Per TESTING.md, subprocess tests run outside the real repo. The runner
    resolves everything from __file__, so an unrelated cwd also proves it
    does not depend on the caller's location.
    """
    return subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_material_negative_scenarios_grade_structured_evidence():
    """False-positive probes must grade review state, not reassuring prose."""
    for scenario_name in ("php_clean_review", "js_clean_review"):
        expected = _eval_mod.SCENARIOS[scenario_name]["expected"]
        for answer_key in expected.values():
            assert answer_key["min_check_count"] == 1
            assert answer_key["max_unclaimed_review_file_count"] == 0


class TestGradeOnlyMode:
    """--grade-only is the entry point AGENTS.md documents for grading
    saved review output without model calls."""

    def test_grades_a_passing_review_pair(self, tmp_path):
        _write_review_pair(tmp_path)

        review = json.loads((tmp_path / "security-review.json").read_text())
        assert len(review["checks"]) == 1
        assert review["checks"][0]["source_reviewers"] == ["security"]
        assert review["unclaimed_review_files"] == []
        assert review["reviewed_file_count"] == 1

        result = _run_eval("--grade-only", str(tmp_path), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr
        assert "security" in result.stdout

    def test_reports_a_failing_review_pair(self, tmp_path):
        """A malformed JSON review is reported, not crashed on."""
        _write_review_pair(tmp_path)
        bad = tmp_path / "security-review.json"
        data = json.loads(bad.read_text())
        del data["verdict"]  # required top-level field
        bad.write_text(json.dumps(data))

        result = _run_eval("--grade-only", str(tmp_path), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "security" in result.stdout

    @pytest.mark.parametrize(
        ("malformation", "diagnostic"),
        [
            ("numeric-summary", "review summary is malformed"),
            ("non-object-finding", "review finding 0 must be an object"),
            (
                "retired-schema-and-field",
                "review has unexpected fields: issues",
            ),
        ],
    )
    def test_rejected_final_review_reports_canonical_diagnostic(
        self, tmp_path, malformation, diagnostic
    ):
        _write_review_pair(tmp_path)
        bad = tmp_path / "security-review.json"
        data = json.loads(bad.read_text())
        if malformation == "numeric-summary":
            data["summary"] = 7
        elif malformation == "non-object-finding":
            data["findings"] = [7]
        else:
            data["schema"] = 1
            data["issues"] = []
        bad.write_text(json.dumps(data))

        grade = run_grade_only(str(tmp_path))["security"]
        result = _run_eval("--grade-only", str(tmp_path), cwd=tmp_path)

        assert grade.passed is False
        assert grade.failures == [
            diagnostic,
            f"File does not exist: {tmp_path / 'security-review.md'}",
        ]
        assert grade.checks_run == 4
        assert grade.checks_passed == 2
        assert "Traceback" not in result.stderr, result.stderr
        assert result.returncode == 0
        assert diagnostic in result.stdout

    def test_grade_only_materializes_missing_markdown(self, tmp_path):
        """Finalized runs may lack derived Markdown until materialization."""
        _write_review_pair(tmp_path)
        assert not (tmp_path / "security-review.md").is_file()

        results = run_grade_only(str(tmp_path))
        assert "security" in results
        # md grade passed: the pair result flattens json + md checks, so no
        # failure may mention the md file — and the rendered md must itself
        # pass the markdown grader (same grader grade_output_pair delegates to).
        result = results["security"]
        assert result.passed, result.failures
        assert not any("security-review.md" in failure for failure in result.failures)
        md_grade = grade_review_markdown(str(tmp_path / "security-review.md"))
        assert md_grade.passed, md_grade.failures
        assert (tmp_path / "security-review.md").is_file()

    def test_missing_directory_is_reported_not_crashed(self, tmp_path):
        result = _run_eval("--grade-only", str(tmp_path / "does-not-exist"), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "ERROR" in result.stdout

    def test_no_args_prints_help(self, tmp_path):
        result = _run_eval(cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "--grade-only" in result.stdout


class TestCliModes:
    def test_explicit_empty_report_path_is_rejected(self, tmp_path):
        result = _run_eval("--report-out", "", cwd=tmp_path)

        assert result.returncode == 2
        assert "--report-out" in result.stderr
        assert "empty" in result.stderr

    def test_explicit_default_trials_requires_dispatch(self, tmp_path):
        result = _run_eval("--trials", "1", cwd=tmp_path)

        assert result.returncode == 2
        assert "require --dispatch" in result.stderr

    def test_grade_only_rejects_explicit_default_trials(self, tmp_path):
        result = _run_eval(
            "--grade-only", str(tmp_path), "--trials", "1", cwd=tmp_path,
        )

        assert result.returncode == 2
        assert "--grade-only cannot be combined" in result.stderr

    def test_empty_report_path_is_rejected_before_dispatch(
        self, tmp_path, monkeypatch,
    ):
        calls = []
        agent = "security-reviewer"
        scenario = {
            "agents": [agent],
            "expected": {agent: {"verdict_in": ["approve"]}},
        }

        def fake_dispatch(*args):
            calls.append(args)
            return GradeResult(
                passed=True, score=1.0, detail={"status": "graded"},
            )

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(_eval_mod, "run_dispatch_scenario", fake_dispatch)
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--report-out", "",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 2
        assert calls == []


class TestDispatchReportMetadata:
    def test_entry_status_is_explicit_not_inferred(self, tmp_path, monkeypatch):
        # A multi-trial aggregate where one trial timed out must report
        # status "degraded" with per-trial statuses — consumers filter
        # reviewer-behavior pass rates on status == "graded" without
        # inferring anything from evidence shape. A trial grade with no
        # detail at all (harness gap) reads as harness_error, never as a
        # dispatched run.
        agent = "security-reviewer"
        scenario = {
            "agents": [agent],
            "expected": {agent: {"verdict_in": ["approve"]}},
        }
        trial_grades = iter([
            GradeResult(
                passed=False, score=0.0, detail={"status": "graded"},
            ),
            GradeResult(
                passed=False, score=0.0, detail={"status": "timed_out"},
            ),
            GradeResult(passed=False, score=0.0),
        ])
        report_path = tmp_path / "report.json"

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario", lambda *args: next(trial_grades),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--trials", "3",
                "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 1
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "degraded"
        assert entry["detail"]["per_trial_status"] == [
            "graded", "timed_out", "harness_error"]
        assert "dispatched" not in entry
        assert "dispatch_count" not in entry

    def test_fully_graded_multitrial_entry_reports_graded(
        self, tmp_path, monkeypatch,
    ):
        agent = "security-reviewer"
        scenario = {
            "agents": [agent],
            "expected": {agent: {"verdict_in": ["approve"]}},
        }
        report_path = tmp_path / "report.json"

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario",
            lambda *args: GradeResult(
                passed=True, score=1.0, checks_run=1, checks_passed=1,
                detail={"status": "graded"},
            ),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--trials", "3",
                "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 0
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "graded"
        assert entry["detail"]["per_trial_status"] == ["graded"] * 3

    def test_single_trial_entry_carries_its_detail_status(
        self, tmp_path, monkeypatch,
    ):
        agent = "security-reviewer"
        scenario = {"agents": [agent], "expected": {}}
        report_path = tmp_path / "report.json"

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario",
            lambda *args: GradeResult(
                passed=True, score=1.0, checks_run=1, checks_passed=1,
                detail={"status": "bootstrap_only"},
            ),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 0
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "bootstrap_only"

    def test_harness_error_after_trials_cannot_masquerade_as_graded(
        self, tmp_path, monkeypatch,
    ):
        # Spec-gate finding: entry status must derive from the RESULT's
        # detail, never from the trial list directly — an aggregation
        # exception after all trials completed is a harness_error entry
        # even though every trial dispatched and graded.
        agent = "security-reviewer"
        scenario = {
            "agents": [agent],
            "expected": {agent: {"verdict_in": ["approve"]}},
        }
        report_path = tmp_path / "report.json"

        def boom(_grades):
            raise RuntimeError("aggregation bug")

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario",
            lambda *args: GradeResult(
                passed=True, score=1.0, checks_run=1, checks_passed=1,
                detail={"status": "graded"},
            ),
        )
        monkeypatch.setattr(_eval_mod, "aggregate_detection_trials", boom)
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--trials", "3",
                "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 1
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "harness_error"
        assert entry["passed"] is False

    def test_status_vocabulary_is_pinned(self):
        assert _eval_mod.ENTRY_STATUSES == {
            "graded", "bootstrap_only", "agent_missing", "routing_drift",
            "bootstrap_failed", "cli_missing", "timed_out", "dispatch_error",
            "model_mismatch", "harness_error", "degraded",
        }

    def test_out_of_vocabulary_status_reports_as_harness_error(self):
        # ENTRY_STATUSES is load-bearing: a typo'd stamp or a leaked internal
        # sentinel ("completed") must not flow into status-filtered pass
        # rates as a novel value.
        result = GradeResult(
            passed=True, score=1.0, detail={"status": "completed"},
        )
        assert _eval_mod.entry_status(result) == "harness_error"
        graded = GradeResult(
            passed=True, score=1.0, detail={"status": "graded"},
        )
        assert _eval_mod.entry_status(graded) == "graded"


class TestDispatchIdentity:
    """The benchmark must dispatch the configured reviewer, not generic Claude.

    These pin the measurement instrument itself: the dispatch prompt must
    route through the plugin's canonical subagent via the Agent tool (the
    production mechanism — full frontmatter contract: system prompt, model,
    effort, tools applied by the host), and frontmatter model routing must
    match the canonical registry — a keyed run that silently grades a
    bare-bootstrap session, or an unrepresentative model, measures the wrong
    thing (found live 2026-08-06, after every layer above this one had been
    reviewed diff-by-diff).
    """

    AGENT_MD = (
        "---\n"
        "name: security-reviewer\n"
        "description: reviews security\n"
        "model: sonnet\n"
        "---\n"
        "\n"
        "Trace attack paths before reporting.\n"
    )

    def test_frontmatter_model_is_extracted(self):
        assert _eval_mod.frontmatter_model(self.AGENT_MD) == "sonnet"

    def test_definition_without_frontmatter_has_no_model(self):
        assert _eval_mod.frontmatter_model("Just instructions.") is None

    def test_prompt_carries_bootstrap_cmd_and_contract(self):
        prompt = _eval_mod.build_dispatch_prompt(
            "security-reviewer", "python3 /x/bootstrap.py --agent security-reviewer",
        )
        assert "python3 /x/bootstrap.py --agent security-reviewer" in prompt
        assert "scope and output contract" in prompt

    def test_dispatch_cmd_runs_the_session_as_the_plugin_agent(self):
        # --agent makes the session BE the reviewer (no orchestrating parent
        # whose artifacts could be misattributed); the shim --plugin-dir
        # resolves the namespaced agent to the WORKTREE definitions (the
        # plugin dir itself carries no manifest, so pointing at it resolves
        # nothing and the installed user-scope copy would answer instead);
        # --setting-sources project excludes that installed copy plus user
        # hooks/memory; JSON output carries per-run model-usage evidence.
        cmd = _eval_mod.build_dispatch_cmd("/bin/claude", "security-reviewer", "P")
        assert cmd[0] == "/bin/claude"
        agent_flag = cmd.index("--agent")
        assert cmd[agent_flag + 1] == "pirategoat-tools:security-reviewer"
        sources_flag = cmd.index("--setting-sources")
        assert cmd[sources_flag + 1] == "project"
        fmt_flag = cmd.index("--output-format")
        assert cmd[fmt_flag + 1] == "json"
        assert cmd[-1] == "P"
        shim = Path(cmd[cmd.index("--plugin-dir") + 1])
        manifest = json.loads((shim / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == "pirategoat-tools"
        agents_link = shim / "agents"
        assert agents_link.resolve() == (_eval_mod.PLUGIN_ROOT / "agents").resolve()

    @pytest.mark.parametrize(
        "returncode,is_error",
        [
            pytest.param(0, True, id="json-session-error"),
            pytest.param(1, False, id="nonzero-cli-exit"),
        ],
    )
    def test_cli_failure_precedes_model_validation(
        self, monkeypatch, tmp_path, returncode, is_error
    ):
        routed = next(
            agent for agent in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[agent].get("model_tier") or "inherit")
            in _eval_mod._DISPATCHABLE_MODELS
        )
        payload = {
            "is_error": is_error,
            "result": "authentication failed",
            "modelUsage": {},
        }
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )
        monkeypatch.setattr(_eval_mod.shutil, "which", lambda _: "/bin/claude")
        monkeypatch.setattr(
            _eval_mod, "build_dispatch_cmd", lambda *_: ["/bin/claude"]
        )
        monkeypatch.setattr(_eval_mod.subprocess, "run", lambda *_, **__: completed)

        rc, text, evidence = _eval_mod.dispatch_agent(
            routed, "bootstrap", str(tmp_path)
        )

        assert rc == 1
        assert text == "authentication failed"
        assert evidence["status"] == "dispatch_error"

    def test_primary_model_attribution_beats_membership(self):
        # modelUsage is a session accumulator including auxiliary calls — a
        # small right-family aux call must not vouch for a main loop that
        # ran on another model.
        routed = next(
            a for a in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit")
            in _eval_mod._DISPATCHABLE_MODELS
        )
        tier = _eval_mod.AGENT_CONFIG[routed]["model_tier"]
        usage = {
            f"claude-{tier}-5": {"outputTokens": 10},
            "claude-other-model": {"outputTokens": 90000},
        }
        error = _eval_mod.check_dispatched_models(routed, usage)
        assert error is not None and "primary" in error
        # Membership fallback still applies when usage carries no weights.
        assert _eval_mod.check_dispatched_models(
            routed, {f"claude-{tier}-5": {}},
        ) is None
        # Empty usage fails closed for a routed tier.
        assert _eval_mod.check_dispatched_models(routed, {}) is not None

    def test_capacity_metadata_does_not_affect_primary_model_attribution(self):
        routed = next(
            a for a in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit")
            in _eval_mod._DISPATCHABLE_MODELS
        )
        tier = _eval_mod.AGENT_CONFIG[routed]["model_tier"]
        primary = f"claude-{tier}-5"
        usage = {
            primary: {
                "inputTokens": 6000,
                "outputTokens": 5000,
                "contextWindow": 200000,
                "maxOutputTokens": 64000,
            },
            "auxiliary": {
                "inputTokens": 1,
                "outputTokens": 1,
                "contextWindow": 1000000,
                "maxOutputTokens": 128000,
            },
        }
        assert _eval_mod._primary_model(usage) == primary
        assert _eval_mod.check_dispatched_models(routed, usage) is None

    def test_canonical_model_identity_is_used_for_routing(self):
        routed = next(
            a for a in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit")
            in _eval_mod._DISPATCHABLE_MODELS
        )
        tier = _eval_mod.AGENT_CONFIG[routed]["model_tier"]
        canonical = f"claude-{tier}-5"
        usage = {
            "gateway-primary": {
                "canonicalModel": canonical,
                "inputTokens": 6000,
                "outputTokens": 5000,
            },
        }
        assert _eval_mod._primary_model(usage) == canonical
        assert _eval_mod.check_dispatched_models(routed, usage) is None

    def test_inherit_tier_accepts_any_dispatched_model(self):
        inherit_agents = [
            a for a in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit") == "inherit"
        ]
        for agent in inherit_agents:
            assert _eval_mod.check_dispatched_models(
                agent, {"anything": {"outputTokens": 5}},
            ) is None

    def test_model_routing_drift_is_refused(self):
        drifted = self.AGENT_MD.replace("model: sonnet", "model: opus")
        agent = next(
            a for a in _eval_mod.ALL_AGENTS
            if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit") != "opus"
        )
        error = _eval_mod.check_model_routing(agent, drifted)
        assert error is not None and "drift" in error

    def test_matching_model_routing_passes(self):
        for agent in _eval_mod.ALL_AGENTS:
            path = _eval_mod.PLUGIN_ROOT / "agents" / f"{agent}.md"
            assert _eval_mod.check_model_routing(agent, path.read_text()) is None, (
                f"{agent}: routing check rejects its own canonical definition"
            )

    def test_every_eval_agent_definition_resolves(self):
        # A benchmark agent whose .md goes missing or empty would silently
        # regress the dispatch back into grading generic Claude.
        for agent in _eval_mod.ALL_AGENTS:
            path = _eval_mod.PLUGIN_ROOT / "agents" / f"{agent}.md"
            assert path.is_file(), f"{agent}: no agent definition at {path}"
            text = path.read_text()
            body = _eval_mod._FRONTMATTER_RE.sub("", text, count=1)
            assert body.strip(), f"{agent}: definition body is empty"
            model = _eval_mod.frontmatter_model(text)
            assert model is None or model == "inherit" or (
                model in _eval_mod._DISPATCHABLE_MODELS
            ), f"{agent}: frontmatter model {model!r} is not dispatchable"

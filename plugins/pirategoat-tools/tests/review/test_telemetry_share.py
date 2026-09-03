"""Tests for the machine-local telemetry sharing consent store and upload."""

import base64
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "telemetry_share.py"

sys.path.insert(0, str(SCRIPTS_DIR))

from review import telemetry_share

sys.path.insert(0, str(TESTS_DIR))
from helpers.gh_shim import (
    gh_call_argv,
    gh_requests,
    install_gh_shim,
    user_config_file,
    write_user_config,
)
from helpers.pipeline_process import hermetic_env, init_bare_repo
from helpers.telemetry_run import RECORDED_UNDISCLOSED, write_complete_run


@pytest.fixture(autouse=True)
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


class TestRemoteIdentity:
    """The origin-URL grammar, exercised directly.

    `_remote_identity` is a pure function, so the URL matrix needs no git
    repositories — only the two boundary cases in `TestRepoIdentity` do.
    """

    @pytest.mark.parametrize(
        ("origin", "expected"),
        (
            ("git@github.com:acme/widget.git", "github.com/acme/widget"),
            ("https://github.com/acme/widget.git", "github.com/acme/widget"),
            ("https://github.com/acme/widget", "github.com/acme/widget"),
            (
                "git@github.example.com:acme/widget.git",
                "github.example.com/acme/widget",
            ),
            (
                "ssh://git@git.example.test:8443/acme/widget.git",
                "git.example.test:8443/acme/widget",
            ),
            (
                "https://git.example.test:9443/acme/widget.git",
                "git.example.test:9443/acme/widget",
            ),
        ),
    )
    def test_recognized_origins_yield_host_owner_name(self, origin, expected):
        assert telemetry_share._remote_identity(origin) == expected

    @pytest.mark.parametrize(
        "origin",
        (
            "not-a-url",
            "C:/repos/widget",
            "C:\\repos\\widget",
            "\\\\server\\share\\widget",
            "https://github.com/acme",
            "https://github.com/acme/widget/extra",
        ),
    )
    def test_unrecognized_origins_have_no_identity(self, origin):
        assert telemetry_share._remote_identity(origin) is None


class TestRepoIdentity:
    """The git boundary: reading `origin` off a real repository."""

    def test_origin_remote_yields_the_shareable_identity(self, tmp_path):
        repo = init_bare_repo(tmp_path / "ssh", "git@github.com:acme/widget.git")

        assert telemetry_share.repo_identity(str(repo)) == "github.com/acme/widget"

    def test_no_remote_fails_closed_to_an_empty_identity(self, tmp_path):
        repo = init_bare_repo(tmp_path / "no-origin")

        assert telemetry_share.repo_identity(str(repo)) == ""

    def test_malformed_origin_fails_closed_to_an_empty_identity(self, tmp_path):
        """Syntax no URL parser accepts is still just "no identity"."""
        repo = init_bare_repo(
            tmp_path / "malformed-origin", "ssh://[bad/acme/widget.git"
        )

        assert telemetry_share.repo_identity(str(repo)) == ""

    def test_identity_derivation_is_total_against_an_unexpected_failure(
        self, tmp_path, monkeypatch
    ):
        """Any derivation failure is the one answer "no shareable identity".

        Pinned at this boundary rather than at each caller: `repo_identity`
        owns the fail-closed contract, so a new origin-URL surprise needs no
        new guard anywhere else.
        """
        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(telemetry_share.subprocess, "run", explode)

        assert telemetry_share.repo_identity(str(tmp_path)) == ""


class TestConsentStore:
    def test_defaults_read_as_unset_with_no_config_file(self):
        assert telemetry_share.sharing_state() == "unset"
        assert telemetry_share.repo_consent("acme/widget") == "unset"

    def test_record_sharing_roundtrips_and_preserves_unrelated_keys(self, tmp_path):
        config_path = write_user_config(
            tmp_path / "xdg", {"review": {"refresh_dependencies": True}}
        )

        telemetry_share.record_sharing("enabled")

        assert telemetry_share.sharing_state() == "enabled"
        assert json.loads(config_path.read_text(encoding="utf-8")) == {
            "review": {"refresh_dependencies": True},
            "telemetry": {"sharing": "enabled"},
        }

    def test_record_repo_roundtrips_per_identity(self):
        telemetry_share.record_repo("acme/widget", "include")
        telemetry_share.record_repo("other/widget", "exclude")

        assert telemetry_share.repo_consent("acme/widget") == "include"
        assert telemetry_share.repo_consent("other/widget") == "exclude"
        assert telemetry_share.repo_consent("missing/widget") == "unset"

    @pytest.mark.parametrize(
        ("record", "arguments"),
        (
            ("record_sharing", ("yes",)),
            ("record_sharing", ([],)),
            ("record_repo", ("acme/widget", "enabled")),
            ("record_repo", ("acme/widget", [])),
        ),
    )
    def test_invalid_values_raise_value_error_and_write_nothing(
        self, tmp_path, record, arguments
    ):
        with pytest.raises(ValueError):
            getattr(telemetry_share, record)(*arguments)

        assert not user_config_file(tmp_path / "xdg").exists()

    def test_concurrent_records_preserve_every_choice(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-threads"))
        import threading

        repos = [f"github.com/acme/widget-{index}" for index in range(16)]
        threads = [
            threading.Thread(
                target=telemetry_share.record_repo, args=(repo, "include")
            )
            for repo in repos
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        settings = json.loads(
            user_config_file(tmp_path / "xdg-threads").read_text(encoding="utf-8")
        )
        assert settings["telemetry"]["repos"] == {
            repo: "include" for repo in repos
        }

    @pytest.mark.parametrize("telemetry", (
        {"sharing": True, "repos": []},
        "yes",
    ))
    def test_malformed_config_shapes_read_as_unset(self, tmp_path, telemetry):
        write_user_config(tmp_path / "xdg", {"telemetry": telemetry})

        assert telemetry_share.sharing_state() == "unset"
        assert telemetry_share.repo_consent("acme/widget") == "unset"


@pytest.fixture
def telemetry_run(tmp_path):
    """One complete PR review written by the real producers.

    See ``helpers.telemetry_run``: every manifest section and lifecycle
    event a finished run emits is present, so the redaction tests and the
    string-path ratchet see the producer's whole upload surface rather
    than a start-plus-finalize skeleton.
    """
    repo = init_bare_repo(tmp_path / "widget", "https://github.com/acme/widget.git")
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    return write_complete_run(
        repo, output_dir, tmp_path / "logs", run_id="20260901T102400Z-telemetry"
    )


def _install_gh_shim(tmp_path, monkeypatch, **shim_options):
    """Install the shared gh shim on PATH; returns its call log."""
    call_log = tmp_path / "gh-calls.jsonl"
    bin_dir = install_gh_shim(tmp_path / "bin", call_log, **shim_options)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return call_log


def _seed_consent(tmp_path):
    """Write enabled + include consent for the fixture repo's identity."""
    config_home = tmp_path / "xdg-consent"
    write_user_config(config_home, {
        "telemetry": {"sharing": "enabled", "repos": {FIXTURE_REPO: "include"}},
    })
    return str(config_home)




def _put_requests(call_log):
    """Every Contents-API PUT as ``(remote path, decoded JSON body)``."""
    return [
        (argv[len(_PUT)], json.loads(body))
        for argv, body in gh_requests(call_log)
        if argv[:len(_PUT)] == _PUT
    ]


def _payloads(telemetry_run):
    """The fixture's manifest and JSONL lines as currently on disk."""
    manifest = json.loads(telemetry_run["manifest_path"].read_text(encoding="utf-8"))
    lines = telemetry_run["log_path"].read_text(encoding="utf-8").splitlines(keepends=True)
    return manifest, lines


def _run_cli(tmp_path, *args, **env):
    """Run the consent-store CLI with ``env`` layered on the inherited one.

    `hermetic_env` supplies the isolating XDG_CONFIG_HOME default, so a
    caller that forgets to override it still cannot read — or write — the
    developer's real machine-local consent file.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=hermetic_env(**env),
        check=False,
    )


# The recorded identity of every telemetry_run fixture's repository.
FIXTURE_REPO = "github.com/acme/widget"
# Every key path in the fixture's redacted payload that carries a string, with
# agent-name dict keys collapsed to <agent>, dispatch-status keys to <status>,
# and list indices to []. The fixture is one complete run, so this is the
# producer's whole string surface; adding a path here is a disclosure
# decision: the step-12 prompt must name what it carries. Enumerated
# diagnostic codes (dispatch `invalid_reason_codes`, a critic verdict row)
# absent from a valid complete run are not listed, and the walker is an
# equality, not a superset, so a stale entry fails as loudly as a new one.
# See TestRedaction.test_every_string_bearing_key_path_is_declared.
DECLARED_STRING_PATHS = frozenset({
    # Disclosed identifiers: repository, target, commit range and SHAs,
    # run id, plugin version (output_dir and session_id are nulled, so
    # absent here).
    "jsonl.agent_complete.run_id",
    "jsonl.agent_review_draft_saved.run_id",
    "jsonl.agent_start.run_id",
    "jsonl.pipeline_end.run_id",
    "jsonl.pipeline_end.snapshot.context.git_range",
    "jsonl.pipeline_start.pipeline.git.base_sha",
    "jsonl.pipeline_start.pipeline.git.head_sha",
    "jsonl.pipeline_start.pipeline.git.requested_range",
    "jsonl.pipeline_start.pipeline.plugin_version",
    "jsonl.pipeline_start.pipeline.pr_number",
    "jsonl.pipeline_start.pipeline.repo",
    "jsonl.pipeline_start.pipeline.repo_path",
    "jsonl.pipeline_start.pipeline.target",
    "jsonl.pipeline_start.run_id",
    "jsonl.step.run_id",
    "manifest.agents.completed[].run_id",
    "manifest.agents.started[].run_id",
    "manifest.run.git.base_sha",
    "manifest.run.git.head_sha",
    "manifest.run.git.requested_range",
    "manifest.run.id",
    "manifest.run.plugin_version",
    "manifest.run.repo",
    "manifest.run.repo_path",
    "manifest.run.target",
    "manifest.steps[].run_id",
    # Pipeline step timings, skips, and status flags: event names, phases,
    # step titles, skip conditions, timestamps, outcome and derived-Markdown
    # statuses, and the verdict's source.
    "jsonl.pipeline_end.event",
    "jsonl.pipeline_end.phase",
    "jsonl.pipeline_end.timestamp",
    "jsonl.pipeline_end.title",
    "jsonl.pipeline_end.snapshot.context.mode",
    "jsonl.pipeline_end.snapshot.context.pr_size.category",
    "jsonl.pipeline_end.snapshot.context.source",
    "jsonl.pipeline_end.summary.pr_size_category",
    "jsonl.pipeline_start.event",
    "jsonl.pipeline_start.pipeline.mode",
    "jsonl.pipeline_start.timestamp",
    "jsonl.step.event",
    "jsonl.step.phase",
    "jsonl.step.timestamp",
    "jsonl.step.title",
    "manifest.dependency_refresh.status",
    "manifest.findings_markdown.status",
    "manifest.outcome.pipeline_status",
    "manifest.outcome.summary.pr_size_category",
    "manifest.outcome.verdict_source",
    "manifest.reviewer_markdown.status",
    "manifest.run.ended_at",
    "manifest.run.mode",
    "manifest.run.started_at",
    "manifest.skipped_steps[].condition",
    "manifest.skipped_steps[].title",
    "manifest.status",
    "manifest.steps[].event",
    "manifest.steps[].phase",
    "manifest.steps[].timestamp",
    "manifest.steps[].title",
    "manifest.worktree_hygiene.baseline_captured_at",
    "manifest.worktree_hygiene.status",
    # Per-agent dispatch and outcome data: names, domains, model tiers,
    # registry-configured triage checks, statuses, verdicts, review-document
    # content hashes, and the synthesis agents' lifecycle.
    "jsonl.agent_complete.agent",
    "jsonl.agent_complete.event",
    "jsonl.agent_complete.review_digest",
    "jsonl.agent_complete.timestamp",
    "jsonl.agent_complete.verdict",
    "jsonl.agent_review_draft_saved.agent",
    "jsonl.agent_review_draft_saved.event",
    "jsonl.agent_review_draft_saved.review_digest",
    "jsonl.agent_review_draft_saved.timestamp",
    "jsonl.agent_start.agent",
    "jsonl.agent_start.domain",
    "jsonl.agent_start.event",
    "jsonl.agent_start.model_tier",
    "jsonl.agent_start.timestamp",
    "jsonl.pipeline_end.snapshot.agent_results.<agent>.verdict",
    "jsonl.pipeline_end.snapshot.dispatch.agents.<agent>.domain",
    "jsonl.pipeline_end.snapshot.dispatch.agents.<agent>.status",
    "jsonl.pipeline_end.snapshot.dispatch.by_status.<status>[]",
    "jsonl.pipeline_end.snapshot.findings.reconciliation.dispatched_agents[]",
    "jsonl.pipeline_end.snapshot.findings.reconciliation.not_applicable_agents[].name",
    "jsonl.pipeline_end.snapshot.findings.reconciliation.not_applicable_agents[].skip_reason",
    "jsonl.pipeline_end.snapshot.findings.reconciliation.reviewing_agents[]",
    "jsonl.pipeline_end.snapshot.findings.verdict",
    "jsonl.pipeline_end.summary.final_verdict",
    "manifest.agents.completed[].agent",
    "manifest.agents.completed[].event",
    "manifest.agents.completed[].review_digest",
    "manifest.agents.completed[].timestamp",
    "manifest.agents.completed[].verdict",
    "manifest.agents.started[].agent",
    "manifest.agents.started[].domain",
    "manifest.agents.started[].event",
    "manifest.agents.started[].model_tier",
    "manifest.agents.started[].timestamp",
    "manifest.dispatch.agents.<agent>.change",
    "manifest.dispatch.agents.<agent>.configured_planner_checks[]",
    "manifest.dispatch.agents.<agent>.domain",
    "manifest.dispatch.agents.<agent>.final_status",
    "manifest.dispatch.agents.<agent>.initial_status",
    "manifest.dispatch.agents.<agent>.model_tier",
    "manifest.outcome.critic_verdict",
    "manifest.outcome.reconciliation.dispatched_agents[]",
    "manifest.outcome.reconciliation.not_applicable_agents[].name",
    "manifest.outcome.reconciliation.not_applicable_agents[].skip_reason",
    "manifest.outcome.reconciliation.reviewing_agents[]",
    "manifest.outcome.summary.final_verdict",
    "manifest.outcome.verdict",
    "manifest.synthesis_agents.agents[].agent",
    "manifest.synthesis_agents.agents[].completed_at",
    "manifest.synthesis_agents.agents[].started_at",
    "manifest.synthesis_agents.agents[].verdict",
    # Repo-relative paths of the reviewed change and their agent assignment.
    "jsonl.agent_start.scope.paths[]",
    "jsonl.pipeline_end.snapshot.context.changed_files[]",
    "manifest.agents.started[].scope.paths[]",
    "manifest.assignment.assigned_files[]",
    "manifest.assignment.assigned_files_by_agent.<agent>[]",
    "manifest.assignment.changed_files[]",
    "manifest.assignment.file_exclusions[].path",
    "manifest.assignment.file_exclusions[].reason",
    "manifest.assignment.reviewable_files[]",
    "manifest.assignment.semantics",
    "manifest.assignment.unassigned_reviewable_files[]",
    # Token usage by model.
    "manifest.usage.availability.orchestrator",
    "manifest.usage.availability.subagents",
    "manifest.usage.by_agent[].agent",
    "manifest.usage.by_agent[].model",
    "manifest.usage.captured_at",
    "manifest.usage.window.ended_at",
    "manifest.usage.window.started_at",
})
# Every request the uploader makes starts with this exact argv prefix.
_API = ["api", "--hostname", "github.com"]
_PUT = [*_API, "-X", "PUT"]


def _redact_roster(reconciliation):
    """Apply the skip-reason rewrite the shared reader's roster shape needs."""
    for entry in reconciliation["not_applicable_agents"]:
        entry["skip_reason"] = "redacted"


class TestRedaction:
    def test_redaction_is_exactly_the_declared_rewrites_and_strips(self, telemetry_run):
        # The whole redaction, as an exact diff against the complete run:
        # every pop below is unconditional, so a producer that stopped
        # recording a value fails here rather than passing vacuously.
        manifest, jsonl_lines = _payloads(telemetry_run)
        jsonl_lines.append(
            json.dumps(
                {
                    "event": "step",
                    "pipeline": {
                        "repo_path": "decoy/repository",
                        "output_dir": "decoy/output-directory",
                    },
                }
            )
            + "\n"
        )

        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )

        expected_manifest = copy.deepcopy(manifest)
        run = expected_manifest["run"]
        run["repo_path"] = FIXTURE_REPO
        # Nulled, not popped: the shared reader requires both slots.
        run["output_dir"] = None
        run["session_id"] = None
        for decision in expected_manifest["dispatch"]["agents"].values():
            for undisclosed in (
                "initial_reason",
                "final_reason",
                "adjustment_reason",
                "planner_signals",
            ):
                decision.pop(undisclosed)
        for workspace_list in ("new_files", "changed_files", "probe_residue_removed"):
            expected_manifest["worktree_hygiene"].pop(workspace_list)
        refresh = expected_manifest["dependency_refresh"]
        refresh.pop("commands")
        refresh.pop("dirty_files")
        refresh["precheck"].pop("dirty_files")
        _redact_roster(expected_manifest["outcome"]["reconciliation"])
        assert redacted_manifest == expected_manifest

        expected_events = [json.loads(line) for line in jsonl_lines]
        start = expected_events[0]
        start["pipeline"]["repo_path"] = FIXTURE_REPO
        start["pipeline"]["output_dir"] = None
        start["pipeline"]["session_id"] = None
        critic_step = next(e for e in expected_events if e.get("step") == 10)
        critic_step["decisions"].pop("reason")
        end = next(e for e in expected_events if e["event"] == "pipeline_end")
        for undisclosed in (
            "pr_title", "pr_author", "pr_url", "linked_issues", "base_ref", "head_ref",
        ):
            end["snapshot"]["context"].pop(undisclosed)
        for decision in end["snapshot"]["dispatch"]["agents"].values():
            decision.pop("reason")
        end["snapshot"].pop("files")
        _redact_roster(end["snapshot"]["findings"]["reconciliation"])
        # The decoy step event keeps its repo_path (only pipeline_start's is
        # the identity) but any output_dir is a local directory, nulled.
        expected_events[-1]["pipeline"]["output_dir"] = None
        assert [json.loads(line) for line in redacted_jsonl] == expected_events

    def test_every_recorded_undisclosed_value_is_absent_after_redaction(
        self, telemetry_run
    ):
        manifest, jsonl_lines = _payloads(telemetry_run)
        serialized_inputs = json.dumps({"manifest": manifest, "lines": jsonl_lines})
        # The producers really recorded each undisclosed value, so an
        # absence below is the redaction's doing, not a hollow fixture's.
        for recorded in RECORDED_UNDISCLOSED:
            assert recorded in serialized_inputs

        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )

        events = [json.loads(line) for line in redacted_jsonl]
        serialized = json.dumps({"manifest": redacted_manifest, "events": events})
        for recorded in RECORDED_UNDISCLOSED:
            assert recorded not in serialized
        # Slots the shared reader requires survive without their text.
        critic_step = next(e for e in events if e.get("step") == 10)
        assert critic_step["decisions"] == {"critic_skipped": True}
        expected_roster = [{"name": "php-tests-reviewer", "skip_reason": "redacted"}]
        assert redacted_manifest["outcome"]["reconciliation"]["not_applicable_agents"] == expected_roster
        assert events[-1]["snapshot"]["findings"]["reconciliation"]["not_applicable_agents"] == expected_roster
        # Disclosed analytic context still uploads: what was reviewed, by
        # whom, and each workspace check's outcome without its file names.
        assert redacted_manifest["outcome"]["summary"]["pr_size_category"] == "small"
        end_context = events[-1]["snapshot"]["context"]
        assert end_context["pr_number"] == 42
        assert "src/checkout.py" in end_context["changed_files"]
        assert redacted_manifest["assignment"]["assigned_files_by_agent"] == {
            "performance-reviewer": ["src/checkout.py"],
            "security-reviewer": ["src/checkout.py", "src/tax.py"],
        }
        assert redacted_manifest["worktree_hygiene"] == {
            "status": "changed_during_review",
            "baseline_captured_at": "2026-09-01T10:24:00+00:00",
        }
        assert redacted_manifest["dependency_refresh"] == {
            "requested": True,
            "reported": True,
            "status": "completed",
            "tracked_files_dirty": False,
            "precheck": {"tracked_files_dirty": True},
        }

    def test_every_string_bearing_key_path_is_declared(self, telemetry_run):
        """Ratchet on the payload's string surface.

        Every key path that carries a string after redaction is listed
        here. A new producer field that carries text fails this test until
        it is either disclosed in the step-12 prompt (and added here) or
        stripped — never uploaded by default.
        """
        manifest, jsonl_lines = _payloads(telemetry_run)
        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )

        paths = set()

        def walk(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("-reviewer") or path.endswith(".agent_results"):
                        segment = "<agent>"
                    elif path.endswith(".by_status"):
                        segment = "<status>"
                    else:
                        segment = key
                    walk(child, f"{path}.{segment}")
            elif isinstance(value, list):
                for child in value:
                    walk(child, f"{path}[]")
            elif isinstance(value, str):
                paths.add(path)

        walk(redacted_manifest, "manifest")
        for line in redacted_jsonl:
            event = json.loads(line)
            walk(event, f"jsonl.{event['event']}")

        assert paths == DECLARED_STRING_PATHS

    def test_no_absolute_local_path_survives_anywhere(self, telemetry_run, tmp_path):
        manifest, jsonl_lines = _payloads(telemetry_run)

        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )
        serialized = json.dumps(
            {
                "manifest": redacted_manifest,
                "events": [json.loads(line) for line in redacted_jsonl],
            }
        )

        for absolute_prefix in ("/Users/", "/home/", "/private/", str(tmp_path)):
            assert absolute_prefix not in serialized

    def test_local_files_stay_byte_identical(self, telemetry_run):
        manifest_path = telemetry_run["manifest_path"]
        log_path = telemetry_run["log_path"]
        manifest_bytes = manifest_path.read_bytes()
        log_bytes = log_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        jsonl_lines = log_bytes.decode("utf-8").splitlines(keepends=True)
        manifest_before = copy.deepcopy(manifest)
        lines_before = list(jsonl_lines)

        telemetry_share.redact_payloads(manifest, jsonl_lines)

        assert manifest == manifest_before
        assert jsonl_lines == lines_before
        assert manifest_path.read_bytes() == manifest_bytes
        assert log_path.read_bytes() == log_bytes

    @pytest.mark.parametrize(
        "leaked",
        (
            "/Users/someone/secret-probe.patch",
            "/home/alice/repo",
            "/private/var/folders/x/run",
            "/etc/passwd",
            "C:\\Users\\alice\\repo",
            "tool.exe D:/work/checkout",
            "cwd=/tmp/run",
            "cwd:/tmp/run",
            "path:/opt/tool",
            "failed at /opt/tool/bin",
            "copy \\\\server\\share\\file",
            pytest.param("file:///Users/alice/private", id="file-url-posix"),
            pytest.param("see FILE:///home/alice/private", id="embedded-file-url"),
        ),
    )
    def test_a_surviving_local_path_anywhere_fails_the_redaction_closed(
        self, telemetry_run, leaked
    ):
        manifest = json.loads(
            telemetry_run["manifest_path"].read_text(encoding="utf-8")
        )
        # Carried in the disclosed reviewed-diff list, which survives
        # redaction — the guard, not a strip, has to catch it.
        manifest["assignment"] = {"changed_files": [leaked]}

        with pytest.raises(ValueError, match="share-unsafe path"):
            telemetry_share.redact_payloads(manifest, [])

    @pytest.mark.parametrize(
        "harmless",
        (
            "github.com/acme/widget",
            "https://github.com/acme/widget",
            "see https://example.com/docs, then ssh://host/repo",
            "plugins/woocommerce/file.php",
            # Repo-relative paths that merely CONTAIN a home-like segment.
            "src/home/index.py",
            "private/config.php",
            "refs/heads/feature/Users/import",
            "abc123..HEAD",
            "verdict: approve",
        ),
    )
    def test_share_safe_strings_pass_the_guard(self, telemetry_run, harmless):
        manifest = json.loads(
            telemetry_run["manifest_path"].read_text(encoding="utf-8")
        )
        manifest["assignment"] = {"changed_files": [harmless]}

        redacted, _ = telemetry_share.redact_payloads(manifest, [])

        assert redacted["assignment"]["changed_files"] == [harmless]

    def test_missing_run_repo_fails_closed(self, telemetry_run):
        manifest = json.loads(telemetry_run["manifest_path"].read_text(encoding="utf-8"))
        manifest["run"].pop("repo")
        telemetry_run["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(ValueError, match="run.repo"):
            telemetry_share.redact_payloads(manifest, [])
        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO).startswith(
            "skipped: manifest repository"
        )

    def test_manifest_naming_another_repo_than_the_consented_one_never_uploads(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        # Consent is checked against the recorded pipeline-start identity;
        # the payload is read from the sibling manifest. A stale or altered
        # manifest naming a different repository must not ride on the
        # first one's consent.
        manifest = json.loads(telemetry_run["manifest_path"].read_text(encoding="utf-8"))
        manifest["run"]["repo"] = "github.com/other-owner/other-repo"
        telemetry_run["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")
        monkeypatch.setattr(
            telemetry_share, "load_user_settings",
            lambda: {"telemetry": {"sharing": "enabled", "repos": {FIXTURE_REPO: "include"}}},
        )
        call_log = _install_gh_shim(tmp_path, monkeypatch)

        assert telemetry_share.recorded_repo(str(telemetry_run["output_dir"])) == FIXTURE_REPO
        assert telemetry_share.maybe_upload(str(telemetry_run["output_dir"])) == (
            "skipped: manifest repository mismatch"
        )
        assert gh_call_argv(call_log) == []


class TestUploadRun:
    @pytest.mark.parametrize(
        "run_id",
        (
            pytest.param("safe/nested", id="slash"),
            pytest.param("../outside", id="traversal-segment"),
            pytest.param("safe..nested", id="double-dot"),
            pytest.param("run\nid", id="control-character"),
            pytest.param("x" * 257, id="too-long"),
        ),
    )
    def test_unsafe_run_id_is_rejected_before_any_github_call(
        self, telemetry_run, tmp_path, monkeypatch, run_id
    ):
        manifest = json.loads(
            telemetry_run["manifest_path"].read_text(encoding="utf-8")
        )
        manifest["run"]["id"] = run_id
        telemetry_run["manifest_path"].write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        call_log = _install_gh_shim(tmp_path, monkeypatch)

        outcome = telemetry_share._upload_run(
            str(telemetry_run["output_dir"]), FIXTURE_REPO
        )

        assert outcome == "skipped: run id invalid"
        assert gh_call_argv(call_log) == []

    def test_uploads_both_files_to_v1_login_run_id(self, telemetry_run, tmp_path, monkeypatch):
        call_log = _install_gh_shim(tmp_path, monkeypatch)

        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO) == (
            f"shared {telemetry_run['run_id']}"
        )

        calls = gh_call_argv(call_log)
        assert calls.count([*_API, "user", "--jq", ".login"]) == 1
        expected_root = (
            f"repos/{telemetry_share.REMOTE_REPO}/contents/"
            f"v1/vlad/{telemetry_run['run_id']}"
        )
        assert [path for path, _body in _put_requests(call_log)] == [
            f"{expected_root}.manifest.json",
            f"{expected_root}.jsonl",
        ]

    def test_existing_remote_file_is_updated_with_its_sha(self, telemetry_run, tmp_path, monkeypatch):
        call_log = _install_gh_shim(tmp_path, monkeypatch, content_state="existing")

        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO) == (
            f"shared {telemetry_run['run_id']}"
        )

        put_bodies = [body for _path, body in _put_requests(call_log)]
        assert len(put_bodies) == 2
        assert all(body["sha"] == "abc" for body in put_bodies)

    def test_request_bodies_travel_on_stdin_never_argv(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        # A base64 payload in argv exceeds Linux's 128 KiB per-argument cap
        # for any artifact above ~96 KiB — real manifests reach hundreds of
        # KiB — and subprocess would raise E2BIG before gh even started.
        call_log = _install_gh_shim(tmp_path, monkeypatch)
        manifest, jsonl_lines = _payloads(telemetry_run)
        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )
        expected_payloads = [
            json.dumps(redacted_manifest, ensure_ascii=False).encode("utf-8"),
            "".join(redacted_jsonl).encode("utf-8"),
        ]

        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO) == (
            f"shared {telemetry_run['run_id']}"
        )

        puts = [
            (argv, body)
            for argv, body in gh_requests(call_log)
            if argv[:len(_PUT)] == _PUT
        ]
        assert len(puts) == 2
        for (argv, body), payload in zip(puts, expected_payloads):
            assert argv[-2:] == ["--input", "-"]
            assert not any(
                argument.startswith(("content=", "message=", "sha="))
                for argument in argv
            )
            request = json.loads(body)
            assert set(request) == {"message", "content"}  # no sha: new file
            assert base64.b64decode(request["content"]) == payload

    @pytest.mark.parametrize("login", ("123", "true", "null", "0", "a-b-c"))
    def test_json_looking_logins_are_raw_text(
        self, telemetry_run, tmp_path, monkeypatch, login
    ):
        # `--jq .login` prints raw text; these are valid GitHub usernames
        # that JSON-decoding would turn into an int, bool, or None.
        call_log = _install_gh_shim(tmp_path, monkeypatch, login=login)

        assert telemetry_share._upload_run(
            str(telemetry_run["output_dir"]), FIXTURE_REPO
        ) == f"shared {telemetry_run['run_id']}"

        put_paths = [path for path, _body in _put_requests(call_log)]
        assert all(f"/contents/v1/{login}/" in path for path in put_paths)

    @pytest.mark.parametrize("login", ("", "-lead", "a/b", "a b", "{\"login\": \"x\"}"))
    def test_malformed_login_output_fails_closed(
        self, telemetry_run, tmp_path, monkeypatch, login
    ):
        call_log = _install_gh_shim(tmp_path, monkeypatch, login=login)

        assert telemetry_share._upload_run(
            str(telemetry_run["output_dir"]), FIXTURE_REPO
        ) == "skipped: upload failed (invalid login response)"
        assert _put_requests(call_log) == []

    def test_jsonl_failure_after_manifest_upload_reports_a_partial_share(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        # The manifest is the unit of publication — the shared reader
        # measures a complete manifest fully without its JSONL — so once it
        # is remote the run IS shared and the outcome must say so.
        call_log = _install_gh_shim(tmp_path, monkeypatch, fail_jsonl_put=True)

        outcome = telemetry_share._upload_run(
            str(telemetry_run["output_dir"]), FIXTURE_REPO
        )

        assert outcome == (
            f"shared {telemetry_run['run_id']} (manifest only; jsonl upload "
            "failed: gh exited 1; ask Vlad for collaborator access)"
        )
        put_paths = [path for path, _body in _put_requests(call_log)]
        assert [path.rsplit(".", 1)[-1] for path in put_paths] == ["json", "jsonl"]

    def test_every_request_pins_github_com_despite_gh_host(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        # gh honors GH_HOST for any request without an explicit hostname —
        # routinely exported while working against a GitHub Enterprise
        # instance — which would resolve the login and the repository on the
        # wrong server. The pin must be on every request, not just the writes.
        monkeypatch.setenv("GH_HOST", "ghe.example.test")
        call_log = _install_gh_shim(tmp_path, monkeypatch, content_state="existing")

        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO) == (
            f"shared {telemetry_run['run_id']}"
        )

        calls = gh_call_argv(call_log)
        assert len(calls) == 5  # login, two SHA lookups, two PUTs
        assert all(call[:len(_API)] == _API for call in calls)

    def test_permission_failure_cli_prints_safe_collaborator_hint(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        private_stderr = "permission denied for secret-account@example.test token=private"
        _install_gh_shim(
            tmp_path,
            monkeypatch,
            fail_code=7,
            fail_stderr=private_stderr,
        )

        result = _run_cli(
            tmp_path, "upload-run", "--output-dir", str(telemetry_run["output_dir"]),
            XDG_CONFIG_HOME=_seed_consent(tmp_path),
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == (
            "skipped: upload failed (gh exited 7; ask Vlad for collaborator access)\n"
        )
        assert private_stderr not in result.stdout
        assert private_stderr not in result.stderr

    def test_missing_gh_yields_one_skipped_line_and_exit_zero(
        self, telemetry_run, tmp_path
    ):
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        result = _run_cli(
            tmp_path, "upload-run", "--output-dir", str(telemetry_run["output_dir"]),
            PATH=str(empty_bin), XDG_CONFIG_HOME=_seed_consent(tmp_path),
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == "skipped: upload failed (gh unavailable)\n"

    def test_timeout_yields_safe_skipped_outcome(
        self, telemetry_run, monkeypatch
    ):
        private_stderr = "secret-account@example.test token=private"

        def time_out(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(
                cmd=["gh", "api"],
                timeout=telemetry_share.GH_TIMEOUT_SECONDS,
                stderr=private_stderr,
            )

        monkeypatch.setattr(telemetry_share.subprocess, "run", time_out)

        outcome = telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO)

        assert outcome == "skipped: upload failed (gh timed out)"
        assert private_stderr not in outcome

    def test_running_manifest_is_skipped(self, telemetry_run, tmp_path, monkeypatch):
        manifest_path = telemetry_run["manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "running"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        call_log = _install_gh_shim(tmp_path, monkeypatch)

        assert telemetry_share._upload_run(str(telemetry_run["output_dir"]), FIXTURE_REPO) == (
            "skipped: run incomplete"
        )
        assert gh_call_argv(call_log) == []


class TestMaybeUpload:
    @pytest.mark.parametrize(
        ("sharing", "consent", "expected"),
        (
            ("disabled", "include", "skipped: sharing disabled"),
            ("unset", "include", "skipped: consent unset"),
            ("enabled", "unset", "skipped: repo consent unset"),
            ("enabled", "exclude", "skipped: repo excluded"),
            ("enabled", "include", "shared run-id"),
        ),
    )
    def test_each_consent_state_gates_correctly(
        self, monkeypatch, sharing, consent, expected
    ):
        monkeypatch.setattr(
            telemetry_share, "load_user_settings",
            lambda: {"telemetry": {"sharing": sharing, "repos": {FIXTURE_REPO: consent}}},
        )
        monkeypatch.setattr(
            telemetry_share, "recorded_repo", lambda _output_dir: FIXTURE_REPO,
        )
        monkeypatch.setattr(
            telemetry_share, "_upload_run", lambda _output_dir, _repo: "shared run-id"
        )

        assert telemetry_share.maybe_upload("/run") == expected

    def test_consent_binds_to_the_recorded_run_identity_not_the_cwd(
        self, telemetry_run, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-binding"))
        telemetry_share.record_sharing("enabled")
        telemetry_share.record_repo("github.com/other/project", "include")

        outcome = telemetry_share.maybe_upload(str(telemetry_run["output_dir"]))

        assert outcome == "skipped: repo consent unset"

    def test_identity_less_run_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-identity"))
        telemetry_share.record_sharing("enabled")

        outcome = telemetry_share.maybe_upload(str(tmp_path))

        assert outcome == "skipped: repository identity unavailable"


class TestRecordedRepo:
    def test_reads_the_runs_recorded_identity(self, telemetry_run):
        assert telemetry_share.recorded_repo(
            str(telemetry_run["output_dir"])
        ) == "github.com/acme/widget"

    def test_missing_marker_reads_as_empty(self, tmp_path):
        assert telemetry_share.recorded_repo(str(tmp_path)) == ""


class TestCli:
    def _run(self, tmp_path, *args):
        return _run_cli(tmp_path, *args, XDG_CONFIG_HOME=str(tmp_path / "xdg"))

    def test_status_reports_sharing_and_repo_consent(self, tmp_path):
        repo = init_bare_repo(tmp_path / "repo", "https://github.com/acme/widget.git")

        result = self._run(tmp_path, "status", "--repo-path", str(repo))

        assert result.returncode == 0
        assert result.stdout == "sharing=unset\nrepo=github.com/acme/widget consent=unset\n"
        assert result.stderr == ""

    def test_status_reports_malformed_origin_as_unavailable(self, tmp_path):
        repo = init_bare_repo(tmp_path / "malformed-origin", "ssh://[bad/acme/widget.git")

        result = self._run(tmp_path, "status", "--repo-path", str(repo))

        assert result.returncode == 0
        assert result.stdout == "sharing=unset\nrepo=unavailable consent=unavailable\n"
        assert result.stderr == ""

    def test_set_sharing_then_status_reflects_it(self, tmp_path):
        set_result = self._run(tmp_path, "set-sharing", "enabled")
        status_result = self._run(tmp_path, "status")

        assert set_result.returncode == 0
        assert status_result.returncode == 0
        assert status_result.stdout == "sharing=enabled\n"

    def test_upload_run_is_consent_gated(self, tmp_path):
        result = self._run(tmp_path, "upload-run", "--output-dir", str(tmp_path))

        assert result.returncode == 0
        assert result.stdout.strip() == "skipped: consent unset"

    def test_set_repo_on_an_identity_less_repo_fails_with_guidance(self, tmp_path):
        repo = init_bare_repo(tmp_path / "no-origin")

        result = self._run(tmp_path, "set-repo", "--repo-path", str(repo), "include")

        assert result.returncode == 2
        assert "no shareable identity" in result.stderr
        assert not user_config_file(tmp_path / "xdg").exists()

    def test_set_repo_output_dir_binds_to_the_recorded_run_identity(
        self, telemetry_run, tmp_path
    ):
        result = self._run(
            tmp_path, "set-repo",
            "--output-dir", str(telemetry_run["output_dir"]), "include",
        )
        config_path = user_config_file(tmp_path / "xdg")

        assert result.returncode == 0, result.stderr
        assert json.loads(config_path.read_text(encoding="utf-8")) == {
            "telemetry": {"repos": {"github.com/acme/widget": "include"}},
        }

    def test_set_repo_rejects_both_identity_sources_at_once(self, tmp_path):
        result = self._run(
            tmp_path, "set-repo",
            "--repo-path", str(tmp_path), "--output-dir", str(tmp_path),
            "include",
        )

        assert result.returncode == 2
        assert "not allowed with" in result.stderr

    def test_set_repo_derives_identity_from_repo_path(self, tmp_path):
        repo = init_bare_repo(tmp_path / "repo", "git@github.com:acme/widget.git")

        result = self._run(tmp_path, "set-repo", "--repo-path", str(repo), "include")
        config_path = user_config_file(tmp_path / "xdg")

        assert result.returncode == 0
        assert json.loads(config_path.read_text(encoding="utf-8")) == {
            "telemetry": {"repos": {"github.com/acme/widget": "include"}},
        }

"""Tests for the dependency-refresh validating save channel."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "dependency_refresh.py"

sys.path.insert(0, str(SCRIPTS_DIR))

from review import dependency_refresh


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dependency Refresh Test",
            "-c",
            "user.email=dependency-refresh@example.com",
            "commit",
            "-m",
            "Initial commit",
        ],
        check=True,
        capture_output=True,
    )
    return repo


def _request(status="completed", commands=None):
    if commands is None:
        commands = [{
            "directory": ".",
            "command": "repo-tool install --locked",
            "exit_status": "ok",
        }]
    return {"schema": 1, "status": status, "commands": commands}


def _write_request(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestObserveTrackedWorktree:
    def test_clean_tracked_worktree_is_false(self, git_repo):
        assert dependency_refresh.observe_tracked_worktree(git_repo) == {
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

    def test_dirty_tracked_paths_are_reported_and_bounded(self, git_repo):
        for index in range(25):
            path = git_repo / f"tracked-{index:02d}.txt"
            path.write_text("initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "--all"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(git_repo),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Add tracked files",
            ],
            check=True,
            capture_output=True,
        )
        for index in range(25):
            (git_repo / f"tracked-{index:02d}.txt").write_text(
                "changed\n", encoding="utf-8"
            )

        observation = dependency_refresh.observe_tracked_worktree(git_repo)

        assert observation["tracked_files_dirty"] is True
        assert observation["dirty_files"] == [
            f"tracked-{index:02d}.txt" for index in range(20)
        ]

    def test_untracked_files_do_not_make_the_worktree_dirty(self, git_repo):
        (git_repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

        assert dependency_refresh.observe_tracked_worktree(git_repo) == {
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

    def test_git_failure_is_unknown_evidence(self, git_repo, monkeypatch):
        monkeypatch.setattr(
            dependency_refresh.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 1, stdout="", stderr="broken"
            ),
        )

        assert dependency_refresh.observe_tracked_worktree(git_repo) == {
            "tracked_files_dirty": None,
            "dirty_files": [],
        }

    def test_dirty_path_strings_are_bounded(self, git_repo, monkeypatch):
        monkeypatch.setattr(
            dependency_refresh.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, stdout=f" M {'x' * 501}\n", stderr=""
            ),
        )

        assert dependency_refresh.observe_tracked_worktree(git_repo) == {
            "tracked_files_dirty": True,
            "dirty_files": ["x" * 500],
        }


class TestSaveReport:
    @pytest.mark.parametrize(
        ("status", "commands"),
        [
            ("not_needed", []),
            ("completed", None),
            ("partial", None),
            ("failed", None),
        ],
    )
    def test_all_declared_outcomes_are_publishable(
        self, git_repo, tmp_path, status, commands
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        payload = _request(status=status, commands=commands)
        if commands is None and status != "not_needed":
            payload = _request(status=status)
        _write_request(report_path, payload)

        problems = dependency_refresh.save_report(
            output_dir, report_path, git_repo
        )

        saved = json.loads(
            (output_dir / "dependency-refresh.json").read_text(encoding="utf-8")
        )
        assert problems == []
        assert saved == {
            **payload,
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

    def test_failed_refresh_with_dirty_final_state_is_published(
        self, git_repo, tmp_path
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        _write_request(
            report_path,
            _request(
                status="failed",
                commands=[{
                    "directory": ".",
                    "command": "repo-tool install --locked",
                    "exit_status": "failed",
                }],
            ),
        )
        (git_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

        problems = dependency_refresh.save_report(
            output_dir=output_dir,
            report_path=report_path,
            repo_root=git_repo,
        )

        saved = json.loads(
            (output_dir / "dependency-refresh.json").read_text(encoding="utf-8")
        )
        assert problems == []
        assert saved["status"] == "failed"
        assert saved["tracked_files_dirty"] is True
        assert saved["dirty_files"] == ["tracked.txt"]

    def test_unknown_final_state_is_published(self, git_repo, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        _write_request(report_path, _request())
        monkeypatch.setattr(
            dependency_refresh,
            "observe_tracked_worktree",
            lambda _repo_root: {"tracked_files_dirty": None, "dirty_files": []},
        )

        assert dependency_refresh.save_report(
            output_dir, report_path, git_repo
        ) == []
        saved = dependency_refresh.load_dependency_refresh_report(output_dir)
        assert saved["tracked_files_dirty"] is None
        assert saved["dirty_files"] == []

    def test_arbitrary_printable_command_is_reported_without_policy_parsing(
        self, git_repo, tmp_path
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        command = "custom-runner sync --mode surprising && echo reported"
        _write_request(
            report_path,
            _request(commands=[{
                "directory": "workspace/tools",
                "command": command,
                "exit_status": "ok",
            }]),
        )

        assert dependency_refresh.save_report(
            output_dir, report_path, git_repo
        ) == []
        assert dependency_refresh.load_dependency_refresh_report(output_dir)[
            "commands"
        ][0]["command"] == command

    def test_invalid_request_preserves_previous_canonical_bytes(
        self, git_repo, tmp_path
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        canonical = output_dir / "dependency-refresh.json"
        canonical.write_bytes(b'{"existing":true}\n')
        report_path = tmp_path / "request.json"
        _write_request(
            report_path,
            {**_request(), "tracked_files_dirty": False},
        )

        problems = dependency_refresh.save_report(
            output_dir, report_path, git_repo
        )

        assert problems == ["'tracked_files_dirty' is script-owned"]
        assert canonical.read_bytes() == b'{"existing":true}\n'

    def test_write_failure_preserves_previous_canonical_bytes(
        self, git_repo, tmp_path, monkeypatch
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        canonical = output_dir / "dependency-refresh.json"
        canonical.write_bytes(b'{"existing":true}\n')
        report_path = tmp_path / "request.json"
        _write_request(report_path, _request())

        def fail_write(*_args, **_kwargs):
            raise OSError("disk unavailable")

        monkeypatch.setattr(dependency_refresh, "atomic_write_json", fail_write)

        with pytest.raises(OSError, match="disk unavailable"):
            dependency_refresh.save_report(output_dir, report_path, git_repo)
        assert canonical.read_bytes() == b'{"existing":true}\n'


class TestRequestValidation:
    @pytest.mark.parametrize(
        ("mutation", "expected"),
        [
            (lambda value: value.update(schema=2), ["'schema' must be 1"]),
            (lambda value: value.update(schema=True), ["'schema' must be 1"]),
            (
                lambda value: value.update(status="ok"),
                ["'status' must be one of: not_needed, completed, partial, failed"],
            ),
            (
                lambda value: value["commands"][0].update(exit_status="unknown"),
                ["commands[0].exit_status must be one of: ok, failed"],
            ),
            (
                lambda value: value.update(commands=["not-an-object"]),
                ["commands[0] must be an object"],
            ),
            (
                lambda value: value.update(commands=_request()["commands"] * 33),
                ["'commands' must contain at most 32 entries"],
            ),
            (
                lambda value: value["commands"][0].update(directory="d" * 201),
                ["commands[0].directory must contain at most 200 characters"],
            ),
            (
                lambda value: value["commands"][0].update(command="x" * 501),
                ["commands[0].command must contain at most 500 characters"],
            ),
            (
                lambda value: value["commands"][0].update(command="bad\ncommand"),
                ["commands[0].command must be printable"],
            ),
            (
                lambda value: value.update(status="not_needed"),
                ["'not_needed' requires an empty 'commands' list"],
            ),
        ],
    )
    def test_invalid_shapes_are_rejected(self, mutation, expected):
        payload = _request()
        mutation(payload)

        assert dependency_refresh.validate_report_request(payload) == expected

    def test_script_owned_and_unknown_fields_are_collected(self):
        payload = {
            **_request(),
            "tracked_files_dirty": False,
            "dirty_files": [],
            "extra": True,
        }
        payload["commands"][0]["manager"] = "custom"

        assert dependency_refresh.validate_report_request(payload) == [
            "'dirty_files' is script-owned",
            "'tracked_files_dirty' is script-owned",
            "unknown top-level field: 'extra'",
            "unknown commands[0] field: 'manager'",
        ]

    def test_missing_required_fields_are_collected(self):
        assert dependency_refresh.validate_report_request({}) == [
            "missing required field: 'schema'",
            "missing required field: 'status'",
            "missing required field: 'commands'",
        ]

    @pytest.mark.parametrize(
        ("raw", "expected_fragment"),
        [
            pytest.param(b"\xff", "valid UTF-8", id="invalid-utf8"),
            pytest.param(b'{"schema":', "valid JSON", id="malformed-json"),
            pytest.param(b"[]", "JSON object", id="non-object"),
            pytest.param(
                b"x" * (1024 * 1024 + 1),
                "at most 1048576 bytes",
                id="oversized",
            ),
        ],
    )
    def test_invalid_report_files_are_rejected(
        self, git_repo, tmp_path, raw, expected_fragment
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        report_path.write_bytes(raw)

        problems = dependency_refresh.save_report(
            output_dir, report_path, git_repo
        )

        assert any(expected_fragment in problem for problem in problems)
        assert not (output_dir / "dependency-refresh.json").exists()


class TestCanonicalValidation:
    def test_loader_accepts_only_the_complete_canonical_shape(
        self, git_repo, tmp_path
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        _write_request(report_path, _request())
        dependency_refresh.save_report(output_dir, report_path, git_repo)

        assert dependency_refresh.load_dependency_refresh_report(output_dir) == {
            **_request(),
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

    @pytest.mark.parametrize(
        "payload",
        [
            _request(),
            {**_request(), "tracked_files_dirty": "false", "dirty_files": []},
            {**_request(), "tracked_files_dirty": False, "dirty_files": [1]},
            {
                **_request(),
                "tracked_files_dirty": False,
                "dirty_files": ["x" * 501],
            },
            {
                **_request(),
                "tracked_files_dirty": False,
                "dirty_files": [],
                "extra": True,
            },
        ],
    )
    def test_loader_rejects_incomplete_or_invalid_canonical_shapes(
        self, tmp_path, payload
    ):
        (tmp_path / "dependency-refresh.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        assert dependency_refresh.load_dependency_refresh_report(tmp_path) is None


class TestSaveCli:
    def test_success_prints_only_saved_echo(self, git_repo, tmp_path):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        _write_request(report_path, _request())

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "save",
                "--output-dir",
                str(output_dir),
                "--report",
                str(report_path),
            ],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0
        assert proc.stdout == "SAVED dependency-refresh.json\n"
        assert proc.stderr == ""

    def test_invalid_input_prints_each_problem_and_does_not_publish(
        self, git_repo, tmp_path
    ):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        report_path = tmp_path / "request.json"
        _write_request(report_path, {"extra": True})

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "save",
                "--output-dir",
                str(output_dir),
                "--report",
                str(report_path),
            ],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 1
        assert proc.stdout == ""
        assert proc.stderr.splitlines() == [
            "INVALID dependency refresh report: missing required field: 'schema'",
            "INVALID dependency refresh report: missing required field: 'status'",
            "INVALID dependency refresh report: missing required field: 'commands'",
            "INVALID dependency refresh report: unknown top-level field: 'extra'",
        ]
        assert not (output_dir / "dependency-refresh.json").exists()

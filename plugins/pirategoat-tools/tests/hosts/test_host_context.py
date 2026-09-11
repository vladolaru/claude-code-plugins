"""Tests for the host_context.py CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hosts import host_context

PLUGIN_SCRIPTS = (
    Path(__file__).parent.parent.parent / "scripts"
).resolve()


def test_cli_runs_from_absolute_script_path_without_pythonpath(tmp_path):
    """Standalone invocation should work from the repo being reviewed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [
            sys.executable,
            str(PLUGIN_SCRIPTS / "hosts" / "host_context.py"),
            "--repo", str(repo),
            "--output-dir", str(outdir),
        ],
        capture_output=True, text=True, cwd=repo, env=env, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (outdir / "host-context.json").exists()


def test_cli_writes_manifest_to_output_dir_and_stdout_creating_it_if_missing(
    tmp_path, monkeypatch, capsys
):
    """--output-dir that doesn't exist is created, not rejected; the manifest
    lands both on disk and on stdout for piping."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out-does-not-exist"
    assert not outdir.exists()

    rc = host_context.main(["--repo", str(repo), "--output-dir", str(outdir)])

    assert rc == 0
    assert outdir.is_dir()
    manifest_path = outdir / "host-context.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert data["unresolved"] == []
    assert data["banner"] is None
    stdout_json = json.loads(capsys.readouterr().out)
    assert stdout_json["version"] == 1


def test_cli_writes_host_context_into_review_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "woocommerce"
    plugin.mkdir()
    config_dir = repo / ".pirategoat"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "hosts": {
            "runtime": [
                {"name": "woocommerce", "path": "../woocommerce"},
            ],
        },
    }))
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"head_ref": "feature"},
    }))

    rc = host_context.main(["--repo", str(repo), "--output-dir", str(outdir)])

    assert rc == 0
    review_context = json.loads((outdir / "review-context.json").read_text())
    assert review_context["git"]["head_ref"] == "feature"
    assert review_context["host_context"]["resolved"][0]["name"] == "woocommerce"
    assert review_context["host_context"]["resolved"][0]["path"] == str(plugin)


def test_cli_missing_args_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        host_context.main([])
    assert exc.value.code == 2  # argparse's required-argument error
    stderr = capsys.readouterr().err
    assert "--repo" in stderr or "required" in stderr.lower()

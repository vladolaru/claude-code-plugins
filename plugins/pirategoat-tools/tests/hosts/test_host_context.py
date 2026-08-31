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


def _run_cli(*args, cwd=None, env=None):
    env = {**os.environ, **(env or {})}
    env["PYTHONPATH"] = str(PLUGIN_SCRIPTS)
    env.pop("XDG_CACHE_HOME", None)
    return subprocess.run(
        [sys.executable, "-m", "hosts.host_context", *args],
        capture_output=True, text=True, cwd=cwd, env=env, timeout=30,
    )


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


def test_cli_writes_manifest_to_output_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    result = _run_cli(
        "--repo", str(repo),
        "--output-dir", str(outdir),
        env={"HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 0, result.stderr
    manifest_path = outdir / "host-context.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert data["unresolved"] == []
    assert data["banner"] is None


def test_cli_writes_host_context_into_review_context(tmp_path):
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

    result = _run_cli(
        "--repo", str(repo),
        "--output-dir", str(outdir),
        env={"HOME": str(tmp_path / "home")},
    )

    assert result.returncode == 0, result.stderr
    review_context = json.loads((outdir / "review-context.json").read_text())
    assert review_context["git"]["head_ref"] == "feature"
    assert review_context["host_context"]["resolved"][0]["name"] == "woocommerce"
    assert review_context["host_context"]["resolved"][0]["path"] == str(plugin)


def test_main_resolves_review_context_through_canonical_authority(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    canonical_path = outdir / "canonical-review-context.json"
    resolved = []

    def resolve_artifact(run_dir, key):
        resolved.append((Path(run_dir), key))
        return canonical_path

    monkeypatch.setattr(host_context, "artifact_path", resolve_artifact)

    assert host_context.main(
        ["--repo", str(repo), "--output-dir", str(outdir)]
    ) == 0
    assert resolved == [(outdir, "review_context")]
    assert json.loads(canonical_path.read_text())["host_context"]["version"] == 1


def test_cli_missing_args_errors(tmp_path):
    result = _run_cli()
    assert result.returncode != 0
    assert "--repo" in result.stderr or "required" in result.stderr.lower()


def test_cli_stdout_contains_manifest_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    result = _run_cli(
        "--repo", str(repo),
        "--output-dir", str(outdir),
        env={"HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 0
    # stdout has the manifest JSON for piping
    stdout_json = json.loads(result.stdout)
    assert stdout_json["version"] == 1


def test_cli_creates_output_dir_when_missing(tmp_path):
    """--output-dir that doesn't exist should be created, not rejected."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out-does-not-exist"
    assert not outdir.exists()
    result = _run_cli(
        "--repo", str(repo),
        "--output-dir", str(outdir),
        env={"HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 0, result.stderr
    assert outdir.is_dir()
    assert (outdir / "host-context.json").exists()

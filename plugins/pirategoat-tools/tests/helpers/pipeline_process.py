"""Shared helper for invoking review/pipeline.py as a subprocess in tests.

Isolation is load-bearing, not cosmetic. review/pipeline.py --step 2 shells
out to review/workspace_setup.py, which runs `git stash push -u` against
whatever it finds dirty in `cwd`. A test that forgets to isolate `cwd` runs
that against the pytest process's ambient working directory — the real repo
checkout, when pytest is invoked from the repo root. See
.claude/docs/learnings/2026-03-19-isolate-subprocess-tests-from-real-repo.md
for the incident this guards against: uncommitted edits were silently
stashed away by a test that never isolated its subprocess's cwd.

`run_pipeline`'s `cwd` parameter has NO default for exactly this reason:
omitting it must be a TypeError, never a silent fall-through to the real
repo. Every caller MUST supply an isolated tmp git repo — see `init_repo`.
"""

import os
import subprocess
import sys

from conftest import PIPELINE_SCRIPT_PATH


def run_pipeline(*args, cwd, env=None):
    """Invoke review/pipeline.py as a subprocess, isolated to `cwd`.

    `cwd` is a required keyword argument — see module docstring. `env`
    folds in the one variation that used to justify separate, per-class
    `_run` copies (dependency-refresh env overrides) as a keyword
    parameter of this single helper.
    """
    cmd = [sys.executable, str(PIPELINE_SCRIPT_PATH), *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(cwd), env=env,
    )


def init_repo(path):
    """Initialize a minimal git repo for isolated pipeline CLI subprocess tests.

    One commit on the initial branch. Returns `path`.
    """
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=path, capture_output=True, check=True,
    )
    (path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path, capture_output=True, check=True,
    )
    return path


def add_commit(path, filename="second.txt"):
    """Add one more commit on top of `init_repo` so ranges like HEAD~1..HEAD
    resolve. Returns `path`."""
    (path / filename).write_text("more\n")
    subprocess.run(["git", "add", filename], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Add {filename}"],
        cwd=path, capture_output=True, check=True,
    )
    return path


def hermetic_env(**overrides):
    """Env dict that never reads the developer's real
    ~/.config/pirategoat/config.json trust declaration — dependency-refresh
    tests must not pick up the machine's standing opt-in. Pass keyword
    overrides to layer on additional env vars a specific test needs.
    """
    return {**os.environ, "XDG_CONFIG_HOME": "/nonexistent-xdg", **overrides}

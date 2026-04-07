"""Shared test fixtures and helpers for pirategoat-tools tests."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"

# Add scripts/ to sys.path so `from review.agent.output import ...` resolves
# to scripts/review/ (not tests/review/). Must happen before test collection
# caches the test package as `review`.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

PIPELINE_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "review" / "pipeline.py"
PIPELINE_TOTAL_STEPS = 12


@pytest.fixture(autouse=True, scope="session")
def _isolate_telemetry_logs(tmp_path_factory):
    """Redirect telemetry logs to a temp dir so tests never pollute ~/.pirategoat-tools/logs/."""
    log_dir = str(tmp_path_factory.mktemp("telemetry-logs"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", log_dir)
        yield


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("review_pipeline", PIPELINE_SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def pipeline_mod():
    """Session-scoped pipeline module — shared across all pipeline test files."""
    return _load_pipeline_module()


def setup_temp_git_repo(diff_file: str) -> str:
    """Create a temp git repo and apply a diff. Returns repo path."""
    tmp = tempfile.mkdtemp(prefix="test-routing-")
    subprocess.run(["git", "init"], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=tmp, capture_output=True, check=True,
    )

    # Initial commit
    readme = os.path.join(tmp, "README.md")
    with open(readme, "w") as f:
        f.write("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp, capture_output=True, check=True,
    )

    # Apply diff
    result = subprocess.run(
        ["git", "apply", str(diff_file)],
        cwd=tmp, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"git apply failed for {Path(diff_file).name}: {result.stderr}"
    )

    subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "changes"],
        cwd=tmp, capture_output=True, check=True,
    )

    return tmp


@pytest.fixture(scope="module")
def bootstrap_repo():
    """Module-scoped temp git repo from multi-file-realistic.diff.

    Shared across all bootstrap integration tests in a module.
    Created once, cleaned up after all tests in the module complete.
    """
    diff = str(FIXTURES_DIR / "multi-file-realistic.diff")
    repo_path = setup_temp_git_repo(diff)
    yield repo_path
    shutil.rmtree(repo_path, ignore_errors=True)

"""Shared fixtures for host-context tests."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_root():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def make_repo(tmp_path):
    """Create a repo dir with specified files. Returns the repo path."""
    def _make(files: dict[str, str]):
        repo = tmp_path / "repo"
        repo.mkdir()
        for rel_path, content in files.items():
            full = repo / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        return repo
    return _make


@pytest.fixture
def make_json_file():
    def _make(path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    return _make

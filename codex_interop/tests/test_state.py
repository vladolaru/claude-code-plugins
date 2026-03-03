"""Tests for interop state serialization and validation."""

from __future__ import annotations

from pathlib import Path

import json
import pytest

from codex_interop.model import ReconcileError
from codex_interop.state import InteropState


def test_interop_state_round_trips(tmp_path: Path):
    """A valid state file deserializes and preserves deterministic JSON."""
    path = tmp_path / "interop-state.json"
    state = InteropState(
        project_root=tmp_path / "project",
        codex_home=tmp_path / "codex-home",
        selected_plugins=("market/plugin@1.0.0",),
        managed_project_files=("CLAUDE.md", ".codex/config.toml"),
        managed_codex_skill_dirs=("plugin-skill",),
    )
    path.write_text(state.to_json())

    loaded = InteropState.from_path(path)

    assert loaded == state
    assert json.loads(state.to_json())["version"] == 1


def test_interop_state_handles_missing_invalid_and_unsupported_files(tmp_path: Path):
    """State loading fails clearly for malformed or unsupported files."""
    missing = tmp_path / "missing.json"
    assert InteropState.from_path(missing) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    with pytest.raises(ReconcileError, match="Invalid interop state file"):
        InteropState.from_path(invalid)

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(
        json.dumps(
            {
                "version": 999,
                "project_root": str(tmp_path / "project"),
                "codex_home": str(tmp_path / "codex-home"),
            }
        )
    )
    with pytest.raises(ReconcileError, match="Unsupported interop state version"):
        InteropState.from_path(unsupported)

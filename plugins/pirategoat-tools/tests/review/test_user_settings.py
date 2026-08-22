"""Tests for review/user_settings.py — requester-side machine-local settings."""

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from review.user_settings import (
    load_user_settings,
    refresh_dependencies_default,
    user_config_path,
)


def _write_config(tmp_path, monkeypatch, payload):
    config_dir = tmp_path / "xdg" / "pirategoat"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(payload)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


class TestUserConfigPath:
    def test_honors_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert user_config_path() == \
            tmp_path / "xdg" / "pirategoat" / "config.json"

    def test_defaults_to_home_dot_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert user_config_path() == \
            tmp_path / "home" / ".config" / "pirategoat" / "config.json"


class TestLoadUserSettings:
    def test_missing_file_reads_as_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert load_user_settings() == {}

    def test_malformed_json_reads_as_empty(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, "{not json")
        assert load_user_settings() == {}

    def test_non_object_json_reads_as_empty(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, "[1, 2]")
        assert load_user_settings() == {}

    def test_valid_object_loads(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch,
                      json.dumps({"review": {"refresh_dependencies": True}}))
        assert load_user_settings() == \
            {"review": {"refresh_dependencies": True}}


class TestRefreshDependenciesDefault:
    def test_exact_true_opts_in(self):
        assert refresh_dependencies_default(
            {"review": {"refresh_dependencies": True}}) is True

    def test_absent_defaults_off(self):
        assert refresh_dependencies_default({}) is False
        assert refresh_dependencies_default({"review": {}}) is False

    def test_truthy_non_boolean_never_opts_in(self):
        # Trust is never inferred from truthy strings or malformed shapes.
        assert refresh_dependencies_default(
            {"review": {"refresh_dependencies": "yes"}}) is False
        assert refresh_dependencies_default(
            {"review": {"refresh_dependencies": 1}}) is False
        assert refresh_dependencies_default(
            {"review": "refresh_dependencies"}) is False
        assert refresh_dependencies_default(None) is False

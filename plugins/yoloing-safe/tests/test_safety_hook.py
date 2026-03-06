"""Tests for the yoloing-safe PreToolUse safety hook."""

import json
import pytest
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


@pytest.fixture
def hook():
    """Import the hook script as a module."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py"
    spec = spec_from_file_location("safety_hook", str(script))
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNormalizeCommand:
    @pytest.mark.parametrize("input_cmd,expected", [
        ("/bin/rm -rf /", "rm -rf /"),
        ("/usr/bin/git reset --hard", "git reset --hard"),
        ("/usr/local/bin/rm -rf /foo", "rm -rf /foo"),
        ("rm /home/user/bin/rm", "rm /home/user/bin/rm"),
        ("rm  -rf   /foo", "rm -rf /foo"),
        ("", ""),
        ("git status", "git status"),
    ])
    def test_normalize(self, hook, input_cmd, expected):
        assert hook.normalize_command(input_cmd) == expected


class TestLoadConfig:
    def test_defaults_when_no_user_file(self, hook, tmp_path, monkeypatch):
        """load_config returns defaults when no user file exists."""
        monkeypatch.setattr(hook, "USER_CONFIG_PATH", str(tmp_path / "nonexistent.json"))
        config = hook.load_config()
        assert "credential_patterns" in config
        assert "zero_access_paths" in config
        assert "disable_rules" in config
        assert config["disable_rules"] == []

    def test_user_override_replaces_present_keys(self, hook, tmp_path, monkeypatch):
        """User config replaces only the keys that are present."""
        user_config = {"zero_access_paths": ["~/.ssh/", "~/.gnupg/", "~/.aws/"]}
        config_file = tmp_path / "yoloing-safe.json"
        config_file.write_text(json.dumps(user_config))
        monkeypatch.setattr(hook, "USER_CONFIG_PATH", str(config_file))
        config = hook.load_config()
        assert config["zero_access_paths"] == ["~/.ssh/", "~/.gnupg/", "~/.aws/"]
        # Other keys should keep defaults
        assert len(config["credential_patterns"]) > 0

    def test_user_override_does_not_add_unknown_keys(self, hook, tmp_path, monkeypatch):
        """Unknown keys in user config are ignored."""
        user_config = {"unknown_key": "value"}
        config_file = tmp_path / "yoloing-safe.json"
        config_file.write_text(json.dumps(user_config))
        monkeypatch.setattr(hook, "USER_CONFIG_PATH", str(config_file))
        config = hook.load_config()
        assert "unknown_key" not in config

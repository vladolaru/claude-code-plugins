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


class TestAllowlist:
    @pytest.mark.parametrize("command", [
        "git checkout -b feature-branch",
        "git checkout --orphan gh-pages",
        "git restore --staged file.txt",
        "git restore -S file.txt",
        "git clean -n",
        "git clean --dry-run",
        "git clean -fn",
        "git clean -nd",
        "git push --force-with-lease",
        "git push --force-if-includes",
        "git push origin main --force-with-lease",
        "rm -rf /tmp/build",
        "rm -rf /var/tmp/test",
        "rm -rf $TMPDIR/cache",
        "chmod +x script.sh",
        "npm publish --dry-run",
        "twine check dist/*",
    ])
    def test_allowlisted_commands(self, hook, command):
        assert hook.is_allowlisted(hook.normalize_command(command)) is True

    @pytest.mark.parametrize("command", [
        "git checkout -- file.txt",
        "git restore file.txt",
        "git restore --staged --worktree file.txt",
        "git clean -fd",
        "git push --force",
        "rm -rf /home/user",
        "chmod 777 file.txt",
        "npm publish",
    ])
    def test_not_allowlisted(self, hook, command):
        assert hook.is_allowlisted(hook.normalize_command(command)) is False


# ---------------------------------------------------------------------------
# Block Tier — Filesystem Destruction (Task 4)
# ---------------------------------------------------------------------------

class TestDestructiveDeletion:
    """Test detect_destructive_deletion."""

    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -fr /home",
        "rm -r -f /foo",
        "rm --recursive --force /bar",
        "rm -rf /home/user",
        "rm -Rf /var/www",
        "rm -rf /etc",
        "rm -rf ~/",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = hook.detect_destructive_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "rm file.txt",
        "rm -f file.txt",
        "rm -i file.txt",
        "rm -rf /tmp/build",  # would be allowlisted, but detection still returns True
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = hook.detect_destructive_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        if command == "rm -rf /tmp/build":
            # Detection function itself catches it, allowlist is checked first in main loop
            assert detected is True
        else:
            assert detected is False


class TestChainedDeletion:
    """Test detect_chained_deletion."""

    @pytest.mark.parametrize("command", [
        "echo done && rm -rf /",
        "true ; rm -rf /home",
        "false || rm -rf /foo",
        "ls -la && rm -fr /bar",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = hook.detect_chained_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "echo done && echo hello",
        "ls -la ; pwd",
        "rm -rf /",  # direct rm, not chained — handled by destructive_deletion
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = hook.detect_chained_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestAlternativeDeletion:
    """Test detect_alternative_deletion."""

    @pytest.mark.parametrize("command", [
        "find / -delete",
        "find . -exec rm {} \\;",
        "find / -exec rm -rf {} +",
        "ls | xargs rm",
        "find . -name '*.log' | xargs rm",
        'eval "rm -rf /"',
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = hook.detect_alternative_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "find . -name '*.log'",
        "find . -type f",
        "xargs echo",
        "eval 'echo hello'",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = hook.detect_alternative_deletion(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestDiskFormatting:
    """Test detect_disk_formatting."""

    @pytest.mark.parametrize("command", [
        "mkfs.ext4 /dev/sda1",
        "mkfs -t ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "dd if=/dev/urandom of=/dev/nvme0n1",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = hook.detect_disk_formatting(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "dd if=input.bin of=output.bin",
        "dd if=/dev/zero of=./testfile bs=1M count=10",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = hook.detect_disk_formatting(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False

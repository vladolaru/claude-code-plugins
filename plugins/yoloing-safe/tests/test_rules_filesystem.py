"""Filesystem, credentials, self-protection, and path rule tests."""

import json
import os
import subprocess

import pytest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py")


def get_detect(hook, rule_id):
    """Get the detection function for a rule, whether custom or generated."""
    return hook.RULES[rule_id]["_detect"]


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
        detect = get_detect(hook, "destructive_deletion")
        detected, msg = detect(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "rm file.txt",
        "rm -f file.txt",
        "rm -i file.txt",
        "rm -rf /tmp/build",  # would be allowlisted, but detection still returns True
        "echo 'rm -rf /'",
        "printf '%s' 'rm -rf /'",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detect = get_detect(hook, "destructive_deletion")
        detected, _ = detect(cmd, "Bash", {}, hook.DEFAULTS)
        if command == "rm -rf /tmp/build":
            # Detection function itself catches it, allowlist is checked first in main loop
            assert detected is True
        else:
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
        detected, msg = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
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
        detected, _ = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False

    @pytest.mark.parametrize("command", [
        "find .claude/tmp/screenshots -name '*.png' -mtime +7 -delete",
        "find ./build -name '*.tmp' -delete",
        "find . -name '*.pyc' -delete",
        "find . -type f -name '*.tmp' -delete",
        "find $TMPDIR/cache -name '*.log' -delete",
        "find /tmp/workdir -name '*.pyc' -delete",
        "find /var/tmp/ci -name '*.o' -delete",
    ])
    def test_scoped_find_delete_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False

    @pytest.mark.parametrize("command", [
        "find / -name '*.log' -delete",
        "find /home -name '*.bak' -delete",
        "find ~ -name '*.key' -delete",
        "find /etc -name '*.conf' -delete",
    ])
    def test_absolute_find_delete_still_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True


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
        detected, msg = get_detect(hook, "disk_formatting")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "dd if=input.bin of=output.bin",
        "dd if=/dev/zero of=./testfile bs=1M count=10",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "disk_formatting")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestCredentialAccess:
    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "/project/.env"}),
        ("Read", {"file_path": "/project/.env.local"}),
        ("Read", {"file_path": "/home/user/.ssh/id_rsa"}),
        ("Read", {"file_path": "/project/client_secret.json"}),
        ("Read", {"file_path": "/project/token.pickle"}),
        ("Read", {"file_path": "/project/server.pem"}),
        ("Read", {"file_path": "/project/id_ed25519"}),
        ("Read", {"file_path": "/project/tls.key"}),
        ("Edit", {"file_path": "/project/.env", "old_string": "a", "new_string": "b"}),
    ])
    def test_detected(self, hook, tool_name, tool_input):
        detected, msg = get_detect(hook, "credential_access")("", tool_name, tool_input, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "/project/.env.example"}),
        ("Read", {"file_path": "/project/.env.template"}),
        ("Read", {"file_path": "/project/.env.sample"}),
        ("Read", {"file_path": "/project/.envrc"}),
        ("Read", {"file_path": "/project/config.json"}),
        ("Read", {"file_path": "/project/README.md"}),
        ("Bash", {"command": "echo hello"}),
    ])
    def test_not_detected(self, hook, tool_name, tool_input):
        cmd = hook.normalize_command(tool_input.get("command", ""))
        detected, _ = get_detect(hook, "credential_access")(cmd, tool_name, tool_input, hook.DEFAULTS)
        assert detected is False

    def test_bash_cat_env(self, hook):
        """Bash cat .env should be detected."""
        cmd = hook.normalize_command("cat .env.local")
        detected, _ = get_detect(hook, "credential_access")(cmd, "Bash", {"command": "cat .env.local"}, hook.DEFAULTS)
        assert detected is True

    def test_bash_cat_env_example_safe(self, hook):
        """Bash cat .env.example should not be detected."""
        cmd = hook.normalize_command("cat .env.example")
        detected, _ = get_detect(hook, "credential_access")(cmd, "Bash", {"command": "cat .env.example"}, hook.DEFAULTS)
        assert detected is False

    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "/project/.ENV"}),
        ("Read", {"file_path": "/project/.Env"}),
        ("Read", {"file_path": "/project/CLIENT_SECRET.JSON"}),
        ("Read", {"file_path": "/project/ID_RSA"}),
    ])
    def test_case_insensitive_detected(self, hook, tool_name, tool_input):
        """Credential patterns should match regardless of case."""
        detected, msg = get_detect(hook, "credential_access")("", tool_name, tool_input, hook.DEFAULTS)
        assert detected is True, f"Expected case-insensitive match for {tool_input}"

    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "/project/.ENV.SAMPLE"}),
        ("Read", {"file_path": "/project/.ENV.EXAMPLE"}),
        ("Read", {"file_path": "/project/.Env.Template"}),
    ])
    def test_case_insensitive_safe_not_detected(self, hook, tool_name, tool_input):
        """Safe credential patterns should also match case-insensitively."""
        detected, _ = get_detect(hook, "credential_access")("", tool_name, tool_input, hook.DEFAULTS)
        assert detected is False, f"Expected case-insensitive safe match for {tool_input}"


class TestZeroAccessPaths:
    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "~/.ssh/id_rsa"}),
        ("Read", {"file_path": "~/.ssh/config"}),
        ("Read", {"file_path": "~/.gnupg/secring.gpg"}),
        ("Write", {"file_path": "~/.ssh/config", "content": "Host *"}),
        ("Edit", {"file_path": "~/.gnupg/gpg.conf", "old_string": "a", "new_string": "b"}),
    ])
    def test_detected(self, hook, tool_name, tool_input):
        cmd = ""
        detected, msg = get_detect(hook, "zero_access_paths")(cmd, tool_name, tool_input, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    def test_bash_cat_ssh(self, hook):
        """Bash cat ~/.ssh/id_rsa should be detected."""
        cmd = hook.normalize_command("cat ~/.ssh/id_rsa")
        detected, _ = get_detect(hook, "zero_access_paths")(cmd, "Bash", {"command": "cat ~/.ssh/id_rsa"}, hook.DEFAULTS)
        assert detected is True

    def test_expanded_path_detected(self, hook):
        """Expanded absolute paths like /Users/user/.ssh/ should also be detected."""
        home = os.path.expanduser("~")
        config = hook.load_config()
        file_path = f"{home}/.ssh/id_rsa"
        detected, _ = get_detect(hook, "zero_access_paths")("", "Read", {"file_path": file_path}, config)
        assert detected is True

    def test_expanded_aws_detected(self, hook):
        """~/.aws/ expanded path should be detected (new default)."""
        home = os.path.expanduser("~")
        config = hook.load_config()
        file_path = f"{home}/.aws/credentials"
        detected, _ = get_detect(hook, "zero_access_paths")("", "Read", {"file_path": file_path}, config)
        assert detected is True

    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "~/.config/something"}),
        ("Read", {"file_path": "/home/user/project/file.txt"}),
    ])
    def test_not_detected(self, hook, tool_name, tool_input):
        cmd = ""
        detected, _ = get_detect(hook, "zero_access_paths")(cmd, tool_name, tool_input, hook.DEFAULTS)
        assert detected is False

    def test_path_containing_ssh_substring_not_detected(self, hook):
        """A project path that contains '.ssh' as substring should not trigger."""
        config = hook.load_config()
        detected, _ = get_detect(hook, "zero_access_paths")(
            "", "Read", {"file_path": "/workspace/project/.ssh-keys/readme.txt"}, config
        )
        assert detected is False

    def test_path_with_literal_tilde_not_detected(self, hook):
        """A path with literal ~ in a non-home context should not trigger."""
        config = hook.load_config()
        detected, _ = get_detect(hook, "zero_access_paths")(
            "", "Read", {"file_path": "/data/backups/~/.ssh/old_key"}, config
        )
        assert detected is False

    def test_bash_ls_local_safe(self, hook):
        """Bash ls ~/.local should not be detected."""
        cmd = hook.normalize_command("ls ~/.local")
        detected, _ = get_detect(hook, "zero_access_paths")(cmd, "Bash", {"command": "ls ~/.local"}, hook.DEFAULTS)
        assert detected is False

    @pytest.mark.parametrize("tool_name,tool_input", [
        ("Read", {"file_path": "~/.AWS/credentials"}),
        ("Read", {"file_path": "~/.Aws/config"}),
        ("Read", {"file_path": "~/.SSH/id_rsa"}),
        ("Read", {"file_path": "~/.Ssh/config"}),
        ("Read", {"file_path": "~/.GNUPG/secring.gpg"}),
    ])
    def test_case_insensitive_detected(self, hook, tool_name, tool_input):
        """Zero-access paths should match regardless of case."""
        config = hook.load_config()
        detected, msg = get_detect(hook, "zero_access_paths")("", tool_name, tool_input, config)
        assert detected is True, f"Expected case-insensitive match for {tool_input}"

    def test_case_insensitive_expanded_detected(self, hook):
        """Expanded paths with different case should also be detected."""
        home = os.path.expanduser("~")
        config = hook.load_config()
        file_path = f"{home}/.AWS/credentials"
        detected, _ = get_detect(hook, "zero_access_paths")("", "Read", {"file_path": file_path}, config)
        assert detected is True


class TestZeroAccessHomeBypasses:
    """Regression: $HOME and ${HOME} forms must be caught for zero-access paths."""

    @pytest.mark.parametrize("command", [
        "cat $HOME/.ssh/id_rsa",
        "cat ${HOME}/.ssh/id_rsa",
        "cat $HOME/.aws/credentials",
        "cat ${HOME}/.gnupg/secring.gpg",
    ])
    def test_bash_home_var_detected(self, hook, command):
        config = hook.load_config()
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "zero_access_paths")(cmd, "Bash", {"command": command}, config)
        assert detected is True, f"Expected $HOME bypass caught for: {command}"

    @pytest.mark.parametrize("command", [
        "cat $HOME/.config/something",
        "echo $HOME",
        "ls ${HOME}/projects",
    ])
    def test_bash_home_var_safe_not_detected(self, hook, command):
        config = hook.load_config()
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "zero_access_paths")(cmd, "Bash", {"command": command}, config)
        assert detected is False


class TestFindDeleteTraversal:
    """Regression: find with .. traversal must not bypass scoped-root allowance."""

    @pytest.mark.parametrize("command", [
        "find ./../../etc -name '*.conf' -delete",
        "find ./../sensitive -name '*.key' -delete",
        "find ../home -name '*.log' -delete",
        "find .. -name '*.tmp' -delete",
    ])
    def test_traversal_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True, f"Expected traversal blocked for: {command}"

    @pytest.mark.parametrize("command", [
        "find . -name '*.log' -delete",
        "find ./src -name '*.tmp' -delete",
        "find .claude/tmp -name '*.png' -delete",
        "find /tmp/workdir -name '*.pyc' -delete",
    ])
    def test_safe_scoped_still_allowed(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "alternative_deletion")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestSensitiveWriteTarget:
    """Test detect_sensitive_write_target."""

    @pytest.mark.parametrize("file_path", [
        "~/.bashrc",
        "~/.zshrc",
        "~/.profile",
        "~/.bash_profile",
        "~/.zprofile",
        "~/.zshenv",
        "/home/user/.bashrc",
        "/Users/user/.zshrc",
        "/project/.git/hooks/post-commit",
        "/project/.git/hooks/pre-push",
        "~/.gitconfig",
        "~/.npmrc",
        "~/.yarnrc",
    ])
    def test_detected(self, hook, file_path):
        detected, msg = get_detect(hook, "sensitive_write_target")(
            "", "Write", {"file_path": file_path, "content": "x"}, hook.DEFAULTS
        )
        assert detected is True, f"Expected {file_path} to be detected"
        assert msg is not None

    @pytest.mark.parametrize("file_path", [
        "/project/src/app.js",
        "/project/README.md",
        "/project/.gitignore",
        "/tmp/test.txt",
        "~/.config/something.json",
        "/project/.git/config",
        # Project-level dotfiles are routine, not risky
        "/project/.npmrc",
        "/project/.yarnrc",
        "/project/.gitconfig",
    ])
    def test_not_detected(self, hook, file_path):
        detected, _ = get_detect(hook, "sensitive_write_target")(
            "", "Write", {"file_path": file_path, "content": "x"}, hook.DEFAULTS
        )
        assert detected is False, f"Expected {file_path} to NOT be detected"

    @pytest.mark.parametrize("command", [
        "echo hi > ~/.bashrc",
        "echo hi >| ~/.bashrc",
        "cp /tmp/x ~/.npmrc",
        "rm ~/.bashrc",
        "mv ~/.git/hooks/pre-commit /tmp/pre-commit.bak",
    ])
    def test_bash_detected(self, hook, command):
        """Bash writes and destructive mutations to sensitive targets should ask."""
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "sensitive_write_target")(
            cmd, "Bash", {"command": command}, hook.DEFAULTS
        )
        assert detected is True
        assert msg is not None

    def test_bash_safe_read_not_detected(self, hook):
        """Reading sensitive targets via Bash is not a write-target hit."""
        detected, _ = get_detect(hook, "sensitive_write_target")(
            "cat ~/.bashrc", "Bash", {"command": "cat ~/.bashrc"}, hook.DEFAULTS
        )
        assert detected is False

    def test_read_not_affected(self, hook):
        """Read tool should never trigger sensitive_write_target."""
        detected, _ = get_detect(hook, "sensitive_write_target")(
            "", "Read", {"file_path": "~/.bashrc"}, hook.DEFAULTS
        )
        assert detected is False


class TestSelfProtectionInterpreterWrite:
    """Regression: interpreter-based writes to protected paths must be blocked."""

    def _run_hook(self, tool_name, tool_input):
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        return subprocess.run(
            ["python3", SCRIPT],
            input=payload, capture_output=True, text=True, timeout=5,
        )

    def test_python_write_to_plugin_blocked(self):
        plugin_root = str(Path(SCRIPT).parent.parent)
        cmd = f"python3 -c \"open('{plugin_root}/hooks/hooks.json','w').write('{{}}')\""
        r = self._run_hook("Bash", {"command": cmd})
        assert r.returncode == 2

    def test_node_write_to_plugin_blocked(self):
        plugin_root = str(Path(SCRIPT).parent.parent)
        cmd = f"node -e \"require('fs').writeFileSync('{plugin_root}/hooks/hooks.json','{{}}')\""
        r = self._run_hook("Bash", {"command": cmd})
        assert r.returncode == 2

    def test_python_write_elsewhere_not_blocked(self):
        cmd = "python3 -c \"open('/tmp/test.txt','w').write('hello')\""
        r = self._run_hook("Bash", {"command": cmd})
        assert r.returncode == 0


class TestSelfProtectionSymlinkToctou:
    """TOCTOU: ln -s to protected path + write through symlink in compound command."""

    def _run_hook(self, tool_name, tool_input):
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        return subprocess.run(
            ["python3", SCRIPT],
            input=payload, capture_output=True, text=True,
        )

    def test_ln_symlink_to_config_then_write_blocked(self):
        """Compound: ln -s <protected> <tmp> && echo > <tmp> must be blocked."""
        config = os.path.expanduser("~/.claude/yoloing-safe.json")
        r = self._run_hook("Bash", {
            "command": f"ln -s {config} /tmp/config-link && echo '{{}}' > /tmp/config-link"
        })
        assert r.returncode == 2

    def test_ln_symlink_to_plugin_dir_blocked(self):
        """ln -s pointing to plugin directory should be blocked."""
        plugin_root = str(Path(SCRIPT).parent.parent)
        r = self._run_hook("Bash", {
            "command": f"ln -s {plugin_root}/hooks/hooks.json /tmp/hook-link"
        })
        assert r.returncode == 2

    def test_ln_symlink_to_unprotected_path_allowed(self):
        """ln -s to non-protected paths should not be blocked."""
        r = self._run_hook("Bash", {
            "command": "ln -s /tmp/source /tmp/destination"
        })
        # Should NOT be blocked by self-protection (may hit other rules or pass)
        assert r.returncode != 2

    def test_ln_hardlink_to_unprotected_allowed(self):
        """ln (without -s) to non-protected path is fine."""
        r = self._run_hook("Bash", {
            "command": "ln /tmp/source /tmp/destination"
        })
        assert r.returncode == 0


class TestCredentialFalsePositives:
    """Commands that mention credential paths but don't access files."""

    @pytest.mark.parametrize("command", [
        "echo .env",
        "echo ~/.ssh/",
        "printf '%s' .env",
        "export DOTENV=.env",
        "test -f .env",
        "grep -R '.env' README.md",
        "grep -R '~/.ssh/' README.md",
        "rg '.env' README.md",
    ])
    def test_non_file_commands_allowed(self, hook, command):
        result = subprocess.run(
            ["python3", SCRIPT],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Should be allowed but got RC={result.returncode}: {command}"

    @pytest.mark.parametrize("command", [
        "cat .env",
        "cat ~/.ssh/id_rsa",
        "less .env.local",
        "grep foo ~/.ssh/id_rsa",
        "rg foo .env.local",
    ])
    def test_file_commands_still_blocked(self, hook, command):
        result = subprocess.run(
            ["python3", SCRIPT],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True, text=True,
        )
        assert result.returncode == 2, f"Should be blocked but got RC={result.returncode}: {command}"

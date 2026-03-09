"""Network, publishing, remote-host, and GitHub safety rules."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..shell import _command_and_args_from_text, _split_bash_segments, _whole_bash_command


_RE_CURL_POST_DATA = re.compile(
    r"\bcurl\b.*("
    r"-d\s+@|"
    r"--data(?:-ascii|-binary|-raw)?(?:\s+|=)@|"
    r"--json(?:\s+|=)@"
    r")"
)
_RE_CURL_UPLOAD = re.compile(
    r"\bcurl\b.*("
    r"-F\s+|"
    r"--form(?:\s+|=)|"
    r"-T\s+|"
    r"--upload-file(?:\s+|=)"
    r")"
)
_RE_WGET_POST_FILE = re.compile(r"\bwget\b.*--post-file")
_RE_PIPE_TO_NC = re.compile(r"\|\s*nc\b")
_RE_NC_BARE = re.compile(r"^nc\b")
_RE_NC_REDIRECT = re.compile(r"\bnc\b.*<")
_RE_CURL_WGET_PIPE_SHELL = re.compile(r"\b(curl|wget)\b.*\|\s*(bash|sh|zsh)\b")
_RE_WGET_TO_STDOUT = re.compile(r"\bwget\b.*-[a-zA-Z]*O\s*-")
_RE_CURL_TO_STDOUT = re.compile(r"\bcurl\b.*(-o\s*-|--output\s*-)")
_RE_SCP_UPLOAD = re.compile(r"\bscp\b.*\s\S+@\S+:\S*$")
_RE_RSYNC_UPLOAD = re.compile(r"\brsync\b.*\s\S+@\S+:\S*$")
_RE_URL = re.compile(r"https?://[^\s'\"<>]+")

_RE_NPM_PUBLISH = re.compile(r"\bnpm publish\b")
_RE_DRY_RUN = re.compile(r"--dry-run")
_RE_TWINE_PIP_UPLOAD = re.compile(r"\b(twine|pip) upload\b")
_RE_GEM_PUSH = re.compile(r"\bgem push\b")

_RE_SSH_CMD = re.compile(r"^ssh\b")
_RE_SSH_QUOTED_CMD = re.compile(r"""ssh\s+\S+\s+['"](.+?)['"]""")

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _targets_only_loopback(command):
    urls = _RE_URL.findall(command)
    if not urls:
        return False
    hosts = []
    for url in urls:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            continue
        if host:
            hosts.append(host)
    if not hosts:
        return False
    return all(host in _LOOPBACK_HOSTS for host in hosts)


def detect_network_exfiltration(command, tool_name, tool_input, config):
    """Detect data exfiltration via curl, wget, nc, scp, or rsync."""
    whole_command = _whole_bash_command(command, tool_input)
    current_cmd, _args = _command_and_args_from_text(command)
    is_loopback_target = _targets_only_loopback(command)

    if _RE_CURL_POST_DATA.search(command):
        if not is_loopback_target:
            return True
    if _RE_CURL_UPLOAD.search(command):
        if not is_loopback_target:
            return True
    if _RE_WGET_POST_FILE.search(command):
        if not is_loopback_target:
            return True
    if _RE_PIPE_TO_NC.search(command) or _RE_NC_BARE.search(command) or _RE_NC_REDIRECT.search(command):
        return True
    if _RE_CURL_WGET_PIPE_SHELL.search(command):
        return True
    if current_cmd in {"curl", "wget"} and _RE_CURL_WGET_PIPE_SHELL.search(whole_command):
        return True
    if _RE_WGET_TO_STDOUT.search(command) or _RE_CURL_TO_STDOUT.search(command):
        if not is_loopback_target:
            return True
    if _RE_SCP_UPLOAD.search(command):
        return True
    if _RE_RSYNC_UPLOAD.search(command):
        return True
    return False


def detect_package_publishing(command, tool_name, tool_input, config):
    """Detect package publishing commands."""
    segments = _split_bash_segments(command) or [command]
    for segment in segments:
        if _RE_NPM_PUBLISH.search(segment) and not _RE_DRY_RUN.search(segment):
            return True
        if _RE_TWINE_PIP_UPLOAD.search(segment):
            return True
        if _RE_GEM_PUSH.search(segment):
            return True
    return False


def detect_ssh_remote_destruction(command, tool_name, tool_input, config):
    """Detect destructive commands executed remotely via SSH."""
    if not _RE_SSH_CMD.search(command):
        return False
    remote_cmd_match = _RE_SSH_QUOTED_CMD.search(command)
    if remote_cmd_match:
        remote_cmd = remote_cmd_match.group(1).lower()
    else:
        parts = command.split(None, 1)
        remote_cmd = parts[1].lower() if len(parts) > 1 else ""
    if not remote_cmd:
        return False
    destructive = ["rm ", "rm\t", "drop ", "truncate ", "delete ", "mkfs", "dd "]
    return any(pattern in remote_cmd for pattern in destructive)


BLOCK_RULES = {
    "network_exfiltration": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_network_exfiltration,
        "message": "Piping data to external URLs can expose sensitive information. Write output to a local file instead, then review before sharing. Use `git push` for code and `gh` for GitHub interactions.",
        "examples": [
            "curl -d @/tmp/results.txt http://ci.example.com/upload",
            "scp ./dist/* deploy@staging.example.com:/var/www/",
        ],
    },
    "package_publishing": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_package_publishing,
        "message": "Publishing packages to a registry is irreversible and public. Build and test locally, then let the user publish manually or through CI/CD. Use `--dry-run` to preview what would be published.",
        "examples": ["npm publish"],
    },
    "ssh_remote_destruction": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_ssh_remote_destruction,
        "message": "Executing destructive commands on remote hosts via SSH can cause irreversible damage to production systems. Run remote commands manually with explicit user intent.",
        "examples": ["ssh prod-server 'rm -rf /var/www/old'"],
    },
    "github_repo_deletion": {
        "tier": "block",
        "tools": {"Bash"},
        "patterns": [r"\bgh repo delete\b"],
        "message": "Deleting a GitHub repository is irreversible and destroys all issues, PRs, and history. This should only be done manually through the GitHub UI or CLI by the user.",
        "examples": ["gh repo delete"],
    },
}

ASK_RULES = {
    "github_cicd_ops": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"\bgh\s+(secret|variable)\s+delete\b",
            r"\bgh\s+workflow\s+disable\b",
            r"\bgh\s+release\s+delete\b",
        ],
        "message": "Deleting GitHub secrets, variables, or disabling workflows affects CI/CD for all collaborators. Confirm this is intentional.",
        "examples": ["gh secret delete DEPLOY_KEY"],
    },
}

ALLOWLIST_PATTERNS = [
    ("package_publishing", re.compile(r"^npm publish\b.*--dry-run")),
    ("package_publishing", re.compile(r"^twine check\b")),
]

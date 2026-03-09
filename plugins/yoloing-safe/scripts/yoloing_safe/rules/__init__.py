"""Ordered rule assembly for the yoloing-safe hook."""

from __future__ import annotations

from ..registry import build_registry
from . import filesystem, git, network, system


RULES = {
    "destructive_deletion": filesystem.BLOCK_RULES["destructive_deletion"],
    "alternative_deletion": filesystem.BLOCK_RULES["alternative_deletion"],
    "disk_formatting": filesystem.BLOCK_RULES["disk_formatting"],
    "network_exfiltration": network.BLOCK_RULES["network_exfiltration"],
    "credential_access": filesystem.BLOCK_RULES["credential_access"],
    "package_publishing": network.BLOCK_RULES["package_publishing"],
    "ssh_remote_destruction": network.BLOCK_RULES["ssh_remote_destruction"],
    "github_repo_deletion": network.BLOCK_RULES["github_repo_deletion"],
    "zero_access_paths": filesystem.BLOCK_RULES["zero_access_paths"],
    "git_bare_push": git.BLOCK_RULES["git_bare_push"],
    "git_force_push": git.ASK_RULES["git_force_push"],
    "git_hard_reset": git.ASK_RULES["git_hard_reset"],
    "git_discard_changes": git.ASK_RULES["git_discard_changes"],
    "git_destroy_stash": git.ASK_RULES["git_destroy_stash"],
    "git_history_rewrite": git.ASK_RULES["git_history_rewrite"],
    "git_config_changes": git.ASK_RULES["git_config_changes"],
    "git_other_dangerous": git.ASK_RULES["git_other_dangerous"],
    "permission_changes": system.ASK_RULES["permission_changes"],
    "brew_commands": system.ASK_RULES["brew_commands"],
    "docker_destructive": system.ASK_RULES["docker_destructive"],
    "database_destructive": system.ASK_RULES["database_destructive"],
    "terraform_destructive": system.ASK_RULES["terraform_destructive"],
    "github_cicd_ops": network.ASK_RULES["github_cicd_ops"],
    "sensitive_write_target": filesystem.ASK_RULES["sensitive_write_target"],
    "inline_interpreter": system.ASK_RULES["inline_interpreter"],
    "inline_heredoc": system.ASK_RULES["inline_heredoc"],
}

ALLOWLIST_PATTERNS = (
    git.ALLOWLIST_PATTERNS
    + filesystem.ALLOWLIST_PATTERNS
    + system.ALLOWLIST_PATTERNS
    + network.ALLOWLIST_PATTERNS
)

RULES_BY_TOOL = build_registry(RULES)

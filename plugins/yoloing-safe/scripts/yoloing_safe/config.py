"""Config and self-protection helpers for the yoloing-safe hook."""

from __future__ import annotations

import json
import os


DEFAULTS = {
    "credential_patterns": [
        r"\.env\b(?!\.sample|\.example|\.template)",
        r"client_secret\.json",
        r"\.credentials\.json",
        r"token\.pickle",
        r"\.pem$",
        r"id_rsa",
        r"id_ed25519",
        r"id_ecdsa",
        r"\.key$",
        r"\.p12$",
        r"\.pfx$",
        r"\.jks$",
        r"\.keystore$",
    ],
    "credential_safe_patterns": [
        r"\.env\.sample",
        r"\.env\.example",
        r"\.env\.template",
        r"\.envrc$",
    ],
    "zero_access_paths": [
        "~/.ssh/",
        "~/.gnupg/",
        "~/.aws/",
        "~/.config/gcloud/",
    ],
    "disable_rules": [],
}

USER_CONFIG_PATH = os.path.expanduser("~/.claude/yoloing-safe.json")

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_MODULE_DIR)
_PLUGIN_ROOT = os.path.dirname(_SCRIPTS_DIR)

SELF_PROTECTED_PATHS = [
    USER_CONFIG_PATH,
    _PLUGIN_ROOT,
]

NON_DISABLEABLE_RULES = frozenset({
    "destructive_deletion",
    "network_exfiltration",
    "credential_access",
    "zero_access_paths",
})

SELF_PROTECTION_MESSAGE = (
    "This file is part of the safety hook infrastructure. Modifying it could disable "
    "safety protections. Ask the user to make changes manually."
)


def is_path_within_self_protected(real_path: str) -> bool:
    """Check whether a resolved path is in self-protected locations."""
    for protected in SELF_PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        if real_path == protected_real or real_path.startswith(protected_real + os.sep):
            return True
    return False


def is_self_protected_path(file_path: str) -> bool:
    """Check if a file path resolves to a self-protected location."""
    try:
        real_path = os.path.realpath(os.path.expanduser(file_path))
        if is_path_within_self_protected(real_path):
            return True
    except (ValueError, OSError):
        pass
    return False


def load_config() -> dict:
    """Load config: user file overrides present keys, others keep defaults."""
    config = dict(DEFAULTS)
    config_path = os.environ.get("YOLOING_SAFE_CONFIG_PATH", USER_CONFIG_PATH)
    try:
        with open(config_path) as f:
            user = json.load(f)
        for key in DEFAULTS:
            if key in user:
                config[key] = user[key]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    expanded = []
    for path in config["zero_access_paths"]:
        expanded.append(path)
        expanded_path = os.path.expanduser(path)
        if expanded_path != path:
            expanded.append(expanded_path)
            home = os.path.expanduser("~")
            if expanded_path.startswith(home):
                suffix = expanded_path[len(home):]
                expanded.append("$HOME" + suffix)
                expanded.append("${HOME}" + suffix)
    config["zero_access_paths"] = expanded
    return config

"""Parse install overrides from inline JSON or a file."""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hosts.install.runner import validate_extra_args


VALID_JS_MANAGERS = frozenset({"npm", "pnpm", "yarn"})

# Environment variables we allow callers to pass through to install subprocesses.
# Deliberately narrow — covers the common "tell the package manager about my
# private registry / auth" use case and nothing else. Adding keys here requires
# re-justifying why they cannot be abused (e.g. LD_PRELOAD, PATH, HOME would
# alter what binary actually runs).
ALLOWED_ENV_KEY_PREFIXES = (
    "COMPOSER_",
    "NPM_",
    "PNPM_",
    "YARN_",
)
ALLOWED_ENV_KEYS = (
    "NODE_AUTH_TOKEN",
)


@dataclass
class InstallOverrides:
    skip_install: bool = False
    php_args: List[str] = field(default_factory=list)
    js_args: List[str] = field(default_factory=list)
    js_manager_override: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)


def _parse_args_array(section: dict, section_name: str) -> List[str]:
    raw_args = section.get("args", [])
    if raw_args is None:
        return []
    if (
        not isinstance(raw_args, list)
        or any(not isinstance(arg, str) for arg in raw_args)
    ):
        raise ValueError(f"{section_name}.args must be an array of strings")
    return list(raw_args)


def parse_overrides(
    inline_json: Optional[str],
    file_path: Optional[str],
) -> InstallOverrides:
    if inline_json is not None and file_path is not None:
        raise ValueError("Provide exactly one of --overrides-json or --overrides-file")

    data = {}
    if inline_json is not None:
        try:
            data = json.loads(inline_json)
        except json.JSONDecodeError as err:
            raise ValueError(f"Invalid overrides JSON: {err}") from err
    elif file_path is not None:
        try:
            with open(file_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as err:
            raise ValueError(f"Cannot read overrides file {file_path!r}: {err}") from err

    if not isinstance(data, dict):
        raise ValueError(
            f"overrides root must be an object, got {type(data).__name__}"
        )

    if "pre_install" in data:
        raise ValueError(
            "pre_install hooks are not supported — they would execute "
            "arbitrary shell commands from user-supplied JSON."
        )
    if "post_install" in data:
        raise ValueError(
            "post_install hooks are not supported — they would execute "
            "arbitrary shell commands from user-supplied JSON."
        )

    php = data.get("php") or {}
    js = data.get("js") or {}
    if not isinstance(php, dict):
        raise ValueError(f"php override must be an object, got {type(php).__name__}")
    if not isinstance(js, dict):
        raise ValueError(f"js override must be an object, got {type(js).__name__}")

    js_manager = js.get("manager")
    if js_manager is not None and not isinstance(js_manager, str):
        raise ValueError(
            f"js.manager must be a string, got {type(js_manager).__name__}"
        )
    if js_manager is not None and js_manager not in VALID_JS_MANAGERS:
        raise ValueError(
            f"Unknown JS manager: {js_manager!r}. "
            f"Valid: {sorted(VALID_JS_MANAGERS)}"
        )

    env_raw = data.get("env") or {}
    if not isinstance(env_raw, dict):
        raise ValueError(
            f"env override must be an object, got {type(env_raw).__name__}"
        )
    disallowed = [
        k for k in env_raw
        if (
            k not in ALLOWED_ENV_KEYS
            and not any(k.startswith(prefix) for prefix in ALLOWED_ENV_KEY_PREFIXES)
        )
    ]
    if disallowed:
        raise ValueError(
            f"Disallowed env keys: {sorted(disallowed)}. "
            f"Allowed keys: {list(ALLOWED_ENV_KEYS)}. "
            f"Allowed prefixes: {list(ALLOWED_ENV_KEY_PREFIXES)}"
        )

    php_args = _parse_args_array(php, "php")
    js_args = _parse_args_array(js, "js")
    validate_extra_args(php_args)
    validate_extra_args(js_args)

    return InstallOverrides(
        skip_install=bool(data.get("skip_install", False)),
        php_args=php_args,
        js_args=js_args,
        js_manager_override=js_manager,
        env={k: str(v) for k, v in env_raw.items()},
    )

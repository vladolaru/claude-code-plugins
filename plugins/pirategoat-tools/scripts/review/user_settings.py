#!/usr/bin/env python3
"""Requester-side machine-local settings.

``~/.config/pirategoat/config.json`` (or ``$XDG_CONFIG_HOME/pirategoat/
config.json``) holds settings owned by the human running reviews on this
machine — unlike the reviewed repo's ``.pirategoat/config.json``, which is
repo-owned and PR-exposed. Trust declarations belong here precisely because
the reviewed repo must never be able to assert them about itself.

Current settings::

    {"review": {"refresh_dependencies": true}}

``review.refresh_dependencies: true`` declares every interactive run the
requester starts dependency-trusted: trusted-branch dependency refresh
defaults on without a per-run ``--refresh-deps``, overridable per run with
``--no-refresh-deps``. Non-interactive (bot) runs ignore this file entirely —
the pipeline's interactive-only hard-off stays authoritative.
"""

import json
import os
from pathlib import Path


def user_config_path() -> Path:
    """Return $XDG_CONFIG_HOME/pirategoat/config.json or ~/.config/pirategoat/config.json."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "pirategoat" / "config.json"
    return Path(os.path.expanduser("~")) / ".config" / "pirategoat" / "config.json"


def load_user_settings() -> dict:
    """Load settings; missing, malformed, or non-object files read as empty."""
    try:
        with open(user_config_path()) as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def refresh_dependencies_default(settings) -> bool:
    """True when the requester declared interactive runs dependency-trusted.

    Only the exact boolean ``true`` opts in — trust is never inferred from
    truthy strings or malformed shapes.
    """
    if not isinstance(settings, dict):
        return False
    review = settings.get("review")
    if not isinstance(review, dict):
        return False
    return review.get("refresh_dependencies") is True

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

# The consent vocabulary, owned by the reader that decides what counts as
# consent. The writer (``telemetry_share``) validates and offers exactly
# these, so a value can never be accepted by the CLI and then silently
# dropped here as malformed.
SHARING_CHOICES = ("enabled", "disabled")
REPO_CHOICES = ("include", "exclude")


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


def telemetry_settings(settings) -> dict:
    """Return strict, machine-local telemetry sharing settings.

    Sharing needs an explicit ``enabled`` or ``disabled`` choice. Repository
    selections are likewise limited to explicit ``include`` or ``exclude``
    choices; malformed data never becomes consent.
    """
    result = {"sharing": "unset", "repos": {}}
    if not isinstance(settings, dict):
        return result
    telemetry = settings.get("telemetry")
    if not isinstance(telemetry, dict):
        return result
    sharing = telemetry.get("sharing")
    if isinstance(sharing, str) and sharing in SHARING_CHOICES:
        result["sharing"] = sharing
    repos = telemetry.get("repos")
    if not isinstance(repos, dict):
        return result
    result["repos"] = {
        repo: choice
        for repo, choice in repos.items()
        if isinstance(repo, str)
        and isinstance(choice, str)
        and choice in REPO_CHOICES
    }
    return result

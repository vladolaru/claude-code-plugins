"""Cache-root helper for pirategoat's ecosystem cache.

Centralizes XDG_CACHE_HOME/~/.cache resolution for the ecosystem cache.
"""

import os
from pathlib import Path


def pirategoat_cache_root(subdir: str) -> Path:
    """Return $XDG_CACHE_HOME/pirategoat/<subdir> or ~/.cache/pirategoat/<subdir>."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        return Path(cache_home) / "pirategoat" / subdir
    return Path(os.path.expanduser("~")) / ".cache" / "pirategoat" / subdir

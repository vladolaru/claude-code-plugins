"""Shared cache-root helper for pirategoat caches.

Both the ecosystem cache (cache/manager.py) and the install cache
(install/cache.py) must use the same root-resolution logic so users
who set XDG_CACHE_HOME don't end up with two split cache trees.
"""

import os
from pathlib import Path


def pirategoat_cache_root(subdir: str) -> Path:
    """Return $XDG_CACHE_HOME/pirategoat/<subdir> or ~/.cache/pirategoat/<subdir>."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        return Path(cache_home) / "pirategoat" / subdir
    return Path(os.path.expanduser("~")) / ".cache" / "pirategoat" / subdir

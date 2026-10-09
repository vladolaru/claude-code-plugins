from __future__ import annotations

from pathlib import Path

from imgopt_lib.workdirs import cache_root

from . import CORPUS_VERSION

SOURCES_FILE = Path(__file__).resolve().parents[1] / "sources.json"  # the committed pins of every real source


def bench_root() -> Path:
    return cache_root() / "bench"


def corpus_dir() -> Path:
    return bench_root() / f"corpus-v{CORPUS_VERSION}"


def downloads_dir() -> Path:
    return bench_root() / "downloads"  # fetched originals, shared across corpus versions


def runs_dir() -> Path:
    return bench_root() / "runs"

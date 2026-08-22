"""
Shared primitives for reading Codex CLI rollout files.

Codex stores one conversation thread per JSONL file at
~/.codex/sessions/YYYY/MM/DD/rollout-{timestamp}-{thread-id}.jsonl.
Line 1 is a `session_meta` entry carrying the thread's identity, its
working directory, and — for subagents — its position in the thread tree.

This module is the only place that knows the rollout schema. Both
codex_session_analyzer.py and codex_session_metrics.py build on it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

DEFAULT_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# A rollout touched more recently than this is probably still being written.
# Live rollouts grow while being read, so their numbers cannot be trusted.
ACTIVE_WINDOW_SECONDS = 300

ROOT_AGENT_PATH = "/root"

DEFAULT_SINCE_DAYS = 7
DEFAULT_LIMIT = 20


@dataclass
class ThreadMeta:
    """Identity and tree position of one Codex thread, from its line 1."""

    thread_id: str
    session_id: str
    parent_thread_id: str | None
    cwd: str
    agent_role: str
    agent_path: str
    depth: int
    cli_version: str
    path: Path
    mtime: float


def _thread_spawn(payload: dict) -> dict:
    """Extract the thread_spawn block, tolerating every observed source shape.

    `source` is usually a dict, but is sometimes a bare string. Descending
    without type checks raises on roughly 10% of real rollouts.
    """
    source = payload.get("source")
    if not isinstance(source, dict):
        return {}
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return {}
    spawn = subagent.get("thread_spawn")
    return spawn if isinstance(spawn, dict) else {}


def read_thread_meta(path: Path) -> ThreadMeta | None:
    """Read line 1 of a rollout. Returns None if it is not usable session_meta."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
    except OSError:
        return None

    try:
        entry = json.loads(first_line)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(entry, dict) or entry.get("type") != "session_meta":
        return None

    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None

    spawn = _thread_spawn(payload)

    return ThreadMeta(
        thread_id=payload.get("id") or "",
        session_id=payload.get("session_id") or "",
        parent_thread_id=spawn.get("parent_thread_id") or payload.get("parent_thread_id"),
        cwd=payload.get("cwd") or "",
        agent_role=spawn.get("agent_role") or "root",
        agent_path=spawn.get("agent_path") or ROOT_AGENT_PATH,
        depth=spawn.get("depth") or 0,
        cli_version=payload.get("cli_version") or "",
        path=path,
        mtime=path.stat().st_mtime if path.exists() else 0.0,
    )


def _day_dirs(sessions_dir: Path, since_days: int, today: date) -> Iterator[Path]:
    """Yield the YYYY/MM/DD directories inside the window that actually exist.

    Walking only these keeps discovery proportional to the window rather than
    to the ~10k rollouts a long-running install accumulates.
    """
    for offset in range(since_days + 1):
        day = today - timedelta(days=offset)
        candidate = sessions_dir / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if candidate.is_dir():
            yield candidate


def discover_threads(
    sessions_dir: Path,
    since_days: int = DEFAULT_SINCE_DAYS,
    cwd: str | None = None,
    agent: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    include_active: bool = False,
    today: date | None = None,
    now: float | None = None,
) -> list[ThreadMeta]:
    """Find finished threads in the window, newest first, after filtering."""
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []

    today = today or date.today()
    now = now if now is not None else time.time()
    roles = {part.strip() for part in agent.split(",")} if agent else None

    found: list[ThreadMeta] = []
    for day_dir in _day_dirs(sessions_dir, since_days, today):
        for path in day_dir.glob("*.jsonl"):
            if not include_active and (now - path.stat().st_mtime) < ACTIVE_WINDOW_SECONDS:
                continue
            meta = read_thread_meta(path)
            if meta is None:
                continue
            if cwd is not None and meta.cwd != cwd:
                continue
            if roles is not None and meta.agent_role not in roles:
                continue
            found.append(meta)

    found.sort(key=lambda m: m.mtime, reverse=True)
    return found[:limit] if limit else found

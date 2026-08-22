"""
Shared primitives for reading Codex CLI rollout files.

Codex stores one conversation thread per JSONL file at
~/.codex/sessions/YYYY/MM/DD/rollout-{timestamp}-{thread-id}.jsonl.
Line 1 is a `session_meta` entry carrying the thread's identity, its
working directory, and — for subagents — its position in the thread tree.

This module is the only place that knows the rollout schema. It is the
foundation for the planned codex_session_analyzer.py and
codex_session_metrics.py CLIs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

DEFAULT_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# A rollout touched more recently than this is probably still being written.
# Live rollouts grow while being read, so their numbers cannot be trusted.
ACTIVE_WINDOW_SECONDS = 300

ROOT_AGENT_PATH = "/root"
UNKNOWN_AGENT_ROLE = "unknown"

DEFAULT_SINCE_DAYS = 30
DEFAULT_LIMIT = 20


@dataclass
class ThreadMeta:
    """Identity and tree position of one Codex thread, from its line 1.

    A session is resumed by writing a NEW root rollout that keeps the original
    session_id but takes a fresh thread id, so one session_id commonly maps to
    many root rollouts (4920 roots across 631 sessions in the sampled corpus).
    The original root is the one where session_id == thread_id.
    """

    thread_id: str
    session_id: str
    spawn_parent_thread_id: str | None
    resumed_from_thread_id: str | None
    cwd: str
    agent_role: str
    agent_path: str
    depth: int
    cli_version: str
    path: Path
    mtime: float
    is_active: bool = False


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
    agent_path = spawn.get("agent_path") or ROOT_AGENT_PATH
    # A spawn block frequently carries agent_role: null — 2812 of 5326 sampled
    # subagents. Defaulting those to "root" would claim they ARE the session
    # root, which corrupts role filtering and the by-role rollup, so only a
    # thread that really sits at /root gets that label.
    agent_role = spawn.get("agent_role") or (
        "root" if agent_path == ROOT_AGENT_PATH else UNKNOWN_AGENT_ROLE
    )

    return ThreadMeta(
        thread_id=payload.get("id") or "",
        session_id=payload.get("session_id") or "",
        # Two distinct relations, deliberately not merged. The spawn-block value
        # means "the thread that spawned me"; the payload-level value on a root
        # means "the thread I was resumed from". Real data is dominated by the
        # second: 4047 of 4290 resumed roots carry it, so a single merged field
        # would answer "who spawned this?" with a resume pointer most of the time.
        spawn_parent_thread_id=spawn.get("parent_thread_id"),
        resumed_from_thread_id=payload.get("parent_thread_id"),
        cwd=payload.get("cwd") or "",
        agent_role=agent_role,
        agent_path=agent_path,
        depth=spawn.get("depth") or 0,
        cli_version=payload.get("cli_version") or "",
        path=path,
        mtime=path.stat().st_mtime if path.exists() else 0.0,
    )


def _day_dirs(sessions_dir: Path, since_days: int, today: date) -> Iterator[Path]:
    """Yield the YYYY/MM/DD directories inside the window that actually exist.

    Walking only these keeps discovery proportional to the window rather than
    to the ~10k rollouts a long-running install accumulates.

    Codex names these directories by LOCAL date, not UTC — verified against the
    real tree, where local matched 2039/2075 files against UTC's 1858. Do not
    "fix" this to utcnow().

    The window is inclusive on both ends: since_days=7 yields today plus the
    previous seven days, i.e. eight directories.
    """
    for offset in range(since_days + 1):
        day = today - timedelta(days=offset)
        candidate = sessions_dir / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if candidate.is_dir():
            yield candidate


@dataclass
class DiscoveryStats:
    """What discovery skipped, so a caller can explain an empty or thin result."""

    scanned: int = 0
    skipped_active: int = 0
    active_roots_included: int = 0
    skipped_unreadable: int = 0
    dropped_unrooted_threads: int = 0
    dropped_unrooted_sessions: int = 0

    def notes(self) -> list[str]:
        """Human-readable lines worth printing. Empty when nothing was skipped."""
        lines = []
        if self.skipped_active:
            lines.append(
                f"{self.skipped_active} rollout(s) skipped as still being written; "
                f"pass --include-active to include them (their numbers may be inconsistent)."
            )
        if self.active_roots_included:
            lines.append(
                f"{self.active_roots_included} session(s) are still running. Their main thread is "
                f"included so the session stays whole, but its own totals may be incomplete."
            )
        if self.dropped_unrooted_threads:
            lines.append(
                f"{self.dropped_unrooted_threads} thread(s) from "
                f"{self.dropped_unrooted_sessions} session(s) excluded because the session "
                f"started before the window; widen --since to include them."
            )
        return lines


def discover_threads(
    sessions_dir: Path,
    since_days: int = DEFAULT_SINCE_DAYS,
    cwd: str | None = None,
    agent: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    include_active: bool = False,
    today: date | None = None,
    now: float | None = None,
    stats: DiscoveryStats | None = None,
) -> list[ThreadMeta]:
    """Find threads belonging to sessions ROOTED in the window, newest first.

    The window selects whole sessions, not individual threads. A session
    qualifies when one of its root rollouts falls inside it; every thread of a
    qualifying session is then included regardless of its own date, and threads
    whose session is not rooted in the window are excluded.

    Filtering threads by their own date instead would cut trees in half: a
    subagent spawned days after its session started would appear without its
    root. That is safe to rely on because a subagent is never dated earlier
    than its session's first root rollout — 0 exceptions in 5248 sampled
    subagents — so a rooted session's whole tree is already inside the window.

    `cwd` and `agent` filter the returned threads; they do not affect which
    sessions qualify, so narrowing by role still yields threads from complete
    sessions. Pass a DiscoveryStats to learn what was skipped.
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []

    today = today or date.today()
    now = now if now is not None else time.time()
    roles = {part.strip() for part in agent.split(",")} if agent else None
    stats = stats if stats is not None else DiscoveryStats()

    in_window: list[ThreadMeta] = []
    rooted_sessions: set[str] = set()
    for day_dir in _day_dirs(sessions_dir, since_days, today):
        for path in day_dir.glob("*.jsonl"):
            stats.scanned += 1
            meta = read_thread_meta(path)
            if meta is None:
                stats.skipped_unreadable += 1
                continue
            meta.is_active = (now - meta.mtime) < ACTIVE_WINDOW_SECONDS
            if meta.agent_path == ROOT_AGENT_PATH:
                # Membership is decided before the active check on purpose. A
                # session whose root is still being written is still a session
                # in this window, and dropping it would discard every finished
                # subagent beneath it — 242 of them in one real case.
                rooted_sessions.add(meta.session_id)
            if meta.is_active and not include_active:
                if meta.agent_path == ROOT_AGENT_PATH:
                    # Keep a live root: without it the session has no tree and
                    # its finished children would be unreachable. Its own
                    # numbers are flagged rather than trusted.
                    stats.active_roots_included += 1
                else:
                    stats.skipped_active += 1
                    continue
            in_window.append(meta)

    dropped_sessions = set()
    found: list[ThreadMeta] = []
    for meta in in_window:
        if meta.session_id not in rooted_sessions:
            stats.dropped_unrooted_threads += 1
            dropped_sessions.add(meta.session_id)
            continue
        if cwd is not None and meta.cwd != cwd:
            continue
        if roles is not None and meta.agent_role not in roles:
            continue
        found.append(meta)
    stats.dropped_unrooted_sessions = len(dropped_sessions)

    found.sort(key=lambda m: m.mtime, reverse=True)
    return found[:limit] if limit is not None else found


ITEM_COMMAND = "CommandExecution"
ITEM_FILE_CHANGE = "FileChange"
ITEM_MESSAGE = "AgentMessage"
ITEM_COMPACTION = "ContextCompaction"


@dataclass
class ThreadScan:
    """Everything one pass over a rollout can tell you about the thread."""

    commands: int = 0
    failed_commands: int = 0
    files_changed: int = 0
    messages: int = 0
    compactions: int = 0
    duration_seconds: float = 0.0
    model: str | None = None
    total_tokens: int = 0
    cached_input_tokens: int = 0
    input_tokens: int = 0
    malformed_lines: int = 0
    items: list[dict] = field(default_factory=list)


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def scan_thread(path: Path, keep_items: bool = False) -> ThreadScan:
    """Walk a rollout once, collecting counts, timing, model, and token totals.

    Only finished rollouts give trustworthy results; a live file grows while
    being read. discover_threads() filters those out before you get here.
    """
    scan = ThreadScan()
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    last_usage: dict = {}

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                scan.malformed_lines += 1
                continue
            if not isinstance(entry, dict):
                scan.malformed_lines += 1
                continue

            stamp = _parse_timestamp(entry.get("timestamp"))
            if stamp is not None:
                first_ts = first_ts or stamp
                last_ts = stamp

            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue

            if entry.get("type") == "turn_context":
                scan.model = payload.get("model") or scan.model
                continue

            payload_type = payload.get("type")

            if payload_type == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("total_token_usage")
                    if isinstance(usage, dict):
                        # Cumulative for the thread — last one wins, never sum.
                        last_usage = usage
                continue

            if payload_type != "item_completed":
                continue

            item = payload.get("item")
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == ITEM_COMMAND:
                scan.commands += 1
                if item.get("exit_code") not in (0, None):
                    scan.failed_commands += 1
            elif item_type == ITEM_FILE_CHANGE:
                # `changes` is a dict keyed by absolute file path. Verified
                # against real rollouts: dict in 405/405 sampled items, never
                # a list. Counting the item instead of its entries undercounts
                # multi-file edits by roughly 13%.
                changes = item.get("changes")
                scan.files_changed += len(changes) if isinstance(changes, (dict, list)) else 1
            elif item_type == ITEM_MESSAGE:
                scan.messages += 1
            elif item_type == ITEM_COMPACTION:
                scan.compactions += 1

            if keep_items:
                scan.items.append(item)

    if first_ts and last_ts:
        scan.duration_seconds = (last_ts - first_ts).total_seconds()

    scan.total_tokens = last_usage.get("total_tokens", 0)
    scan.cached_input_tokens = last_usage.get("cached_input_tokens", 0)
    scan.input_tokens = last_usage.get("input_tokens", 0)
    return scan


def parent_agent_path(agent_path: str) -> str | None:
    """The parent's agent_path, or None for a root thread.

    agent_path encodes tree position literally, so "/root/a/deep" hangs off
    "/root/a". This is the only correlation mechanism the tools implement:
    it needs nothing beyond line 1 of each rollout.
    """
    if "/" not in agent_path:
        return None
    head = agent_path.rsplit("/", 1)[0]
    return head or None


@dataclass
class ThreadTree:
    """Threads grouped into one tree per SESSION.

    Keyed by (session_id, agent_path), because agent_path is scoped to one
    session and is NOT globally unique — every root thread is "/root". Keying
    on the bare path merges every session in the window into one namespace.

    `roots` holds one entry per session, never one per root rollout. Resuming a
    session writes an additional root rollout under the same session_id, so a
    session commonly has many (4920 root rollouts across 631 sessions in the
    sampled corpus). A resume is a continuation, not a new session, so the
    session is represented once — by its original root where one is present,
    otherwise its earliest — and the remaining rollouts are available from
    `resumes_of()`.
    """

    roots: list[ThreadMeta]
    children_by_parent: dict[tuple[str, str], list[ThreadMeta]] = field(default_factory=dict)
    resumes_by_session: dict[str, list[ThreadMeta]] = field(default_factory=dict)

    def children_of(self, meta: ThreadMeta) -> list[ThreadMeta]:
        """Direct children of one thread. Takes the ThreadMeta, not a path.

        A bare path cannot express which session it belongs to, so passing one
        would reintroduce the collision this keying exists to prevent.
        """
        return self.children_by_parent.get((meta.session_id, meta.agent_path), [])

    def resumes_of(self, meta: ThreadMeta) -> list[ThreadMeta]:
        """The session's additional root rollouts, oldest first, excluding `meta`.

        Deliberately NOT summed into the session's totals. A resume rollout
        replays the prior context, so its token count is largely re-sent rather
        than new work — one real session showed six resumes carrying 10-21M
        tokens each while executing zero commands, against the original's 104M
        tokens and 1157 commands. Adding them would inflate the session badly.
        """
        return self.resumes_by_session.get(meta.session_id, [])


def _session_representative(rollouts: list[ThreadMeta]) -> ThreadMeta:
    """The rollout that stands for the session: the original, else the earliest.

    The original is identifiable because Codex gives the first root rollout a
    thread id equal to the session id; resumes keep the session id but take a
    fresh thread id.
    """
    for meta in rollouts:
        if meta.thread_id == meta.session_id:
            return meta
    return min(rollouts, key=lambda m: m.mtime)


def build_tree(metas: list[ThreadMeta]) -> ThreadTree:
    """Group threads into one tree per session, collapsing resumes."""
    known = {(meta.session_id, meta.agent_path) for meta in metas}
    children_by_parent: dict[tuple[str, str], list[ThreadMeta]] = {}
    root_rollouts: dict[str, list[ThreadMeta]] = {}

    for meta in metas:
        parent = parent_agent_path(meta.agent_path)
        if parent is None:
            root_rollouts.setdefault(meta.session_id, []).append(meta)
        elif (meta.session_id, parent) in known:
            children_by_parent.setdefault((meta.session_id, parent), []).append(meta)
        # A thread whose parent is absent is dropped here rather than promoted to
        # a root: discover_threads only returns threads from sessions that are
        # rooted in the window, so this can only be a genuinely broken chain.

    roots: list[ThreadMeta] = []
    resumes_by_session: dict[str, list[ThreadMeta]] = {}
    for session_id, rollouts in root_rollouts.items():
        representative = _session_representative(rollouts)
        roots.append(representative)
        others = sorted((m for m in rollouts if m is not representative), key=lambda m: m.mtime)
        if others:
            resumes_by_session[session_id] = others

    roots.sort(key=lambda m: m.mtime, reverse=True)
    return ThreadTree(
        roots=roots,
        children_by_parent=children_by_parent,
        resumes_by_session=resumes_by_session,
    )

def find_thread(sessions_dir: Path, thread_id: str) -> ThreadMeta | None:
    """Locate one thread anywhere in the archive by id, ignoring any date window.

    The thread id is part of the rollout filename, so this is a filesystem glob
    rather than a scan of file contents — a targeted lookup costs no more than
    listing directories, however far back the session is.
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return None
    for path in sorted(sessions_dir.glob(f"*/*/*/rollout-*{thread_id}*.jsonl")):
        meta = read_thread_meta(path)
        if meta is not None and meta.thread_id == thread_id:
            return meta

    # The filename convention is a fast path, not a contract. Fall back to
    # reading line 1 of every rollout so a naming change upstream degrades
    # performance rather than breaking lookup outright.
    for path in sorted(sessions_dir.glob("*/*/*/*.jsonl")):
        meta = read_thread_meta(path)
        if meta is not None and meta.thread_id == thread_id:
            return meta
    return None


def discover_session(
    sessions_dir: Path,
    thread_id: str,
    include_active: bool = False,
    now: float | None = None,
    stats: DiscoveryStats | None = None,
) -> list[ThreadMeta]:
    """Every thread of the session containing `thread_id`, newest first.

    No date window applies. Naming a thread already says which session you
    want, so bounding the answer by a window could only hide part of it. The
    id may be the session's own id, its root thread, or any subagent within it.

    Scanning is bounded by the session's own lifetime: the original root shares
    the session id and so is findable by the same glob, and no subagent is ever
    dated earlier than it, so only the days from the root onward are read.
    """
    sessions_dir = Path(sessions_dir)
    target = find_thread(sessions_dir, thread_id)
    if target is None:
        return []

    session_id = target.session_id
    origin = find_thread(sessions_dir, session_id) or target
    now = now if now is not None else time.time()
    stats = stats if stats is not None else DiscoveryStats()

    # Day directories are named by date, so a lexical compare orders them.
    first_day = "/".join(origin.path.parts[-4:-1])

    found: list[ThreadMeta] = []
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if "/".join(day_dir.parts[-3:]) < first_day:
            continue
        for path in day_dir.glob("*.jsonl"):
            stats.scanned += 1
            meta = read_thread_meta(path)
            if meta is None:
                stats.skipped_unreadable += 1
                continue
            if meta.session_id != session_id:
                continue
            meta.is_active = (now - meta.mtime) < ACTIVE_WINDOW_SECONDS
            if meta.is_active and not include_active:
                if meta.agent_path == ROOT_AGENT_PATH:
                    stats.active_roots_included += 1
                else:
                    stats.skipped_active += 1
                    continue
            found.append(meta)

    found.sort(key=lambda m: m.mtime, reverse=True)
    return found

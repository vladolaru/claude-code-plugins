"""Shared vocabulary for the review pipeline modules."""

import hashlib
import re
import subprocess
from pathlib import Path

from .run_paths import artifact_path


SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parents[1]
AGENTS_DIR = PLUGIN_ROOT / "agents"

HOST_CLAUDE = "claude"
HOST_CODEX = "codex"
SUPPORTED_HOSTS = (HOST_CLAUDE, HOST_CODEX)

# The pipeline's machine projection of the reconciliation ledger, assembled
# at step 9 and re-assembled at step 11 after the critic adjustments land.
# Spelled here rather than in `orchestration.py` because BOTH sides need it
# and only one direction of that import is legal: `orchestration` imports
# `briefings` (for the coverage renderer), so `briefings` cannot import back.
# A hand-copied second spelling of an artifact filename is how the writer
# and the briefing that names it come apart.
REVIEW_RECORD_MD = artifact_path("", "review_record").name


def _host(config):
    """Return the persisted orchestration host."""
    host = (config or {}).get("host", HOST_CLAUDE)
    return host if host in SUPPORTED_HOSTS else HOST_CLAUDE


def _agent_definition_path(agent_name):
    """Return the canonical reviewer definition path for either host."""
    return AGENTS_DIR / f"{agent_name}.md"


def _codex_task_name(agent_name):
    """Map a reviewer name to Codex's lowercase task-name contract."""
    normalized = re.sub(r"[^a-z0-9_]", "_", str(agent_name).lower())
    if not normalized:
        normalized = "reviewer"
    if normalized[0] not in "abcdefghijklmnopqrstuvwxyz":
        normalized = f"reviewer_{normalized}"
    if len(normalized) > 64:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        prefix_length = 64 - len(digest) - 1
        normalized = f"{normalized[:prefix_length]}_{digest}"
    return normalized


def _codex_agent_instruction(agent_name):
    """Describe how Codex should reuse a canonical Claude agent definition."""
    return (
        f"In the message, first read `{_agent_definition_path(agent_name)}` "
        "completely. Treat its YAML frontmatter as Claude Code packaging "
        "metadata, do not translate its model or tool labels, and follow the "
        "Markdown reviewer instructions."
    )


def _stop_operation(config):
    """Return the host-native operation used to stop a subagent."""
    return "interrupt_agent" if _host(config) == HOST_CODEX else "TaskStop"


# ---------------------------------------------------------------------------
# Step Sequence
# ---------------------------------------------------------------------------

STEP_SEQUENCE = [
    {"step": 1,  "title": "Parse Input",            "phase": "SETUP",      "condition": "always"},
    {"step": 2,  "title": "Repo Setup",              "phase": "SETUP",      "condition": "needs_workspace_setup"},
    {"step": 3,  "title": "Gather Context",           "phase": "SETUP",      "condition": "always"},
    {"step": 4,  "title": "Fetch Issue Context",      "phase": "SETUP",      "condition": "has_unfetched_issues"},
    {"step": 5,  "title": "Dispatch Plan + Triage",   "phase": "EXECUTION",  "condition": "always"},
    {"step": 6,  "title": "Dispatch Agents",          "phase": "EXECUTION",  "condition": "always"},
    {"step": 7,  "title": "Save Review Baseline",     "phase": "EXECUTION",  "condition": "always"},
    {"step": 8,  "title": "Reconcile + Verify",       "phase": "SYNTHESIS",  "condition": "always"},
    {"step": 9,  "title": "Review Record",            "phase": "SYNTHESIS",  "condition": "always"},
    {"step": 10, "title": "Decision Critic",          "phase": "VALIDATION", "condition": "always"},
    {"step": 11, "title": "Author Report + Present Results", "phase": "OUTPUT", "condition": "always"},
    {"step": 12, "title": "Cleanup",                  "phase": "OUTPUT",     "condition": "interactive"},
]

_STEP_MAP = {s["step"]: s for s in STEP_SEQUENCE}

DEFAULT_AGENT_TIMEOUT = 1200  # 20 minutes — matches agents_status.py
CONTEXT_GATHER_TIMEOUT = (2 * 30 * 60) + 60  # two ecosystem-cache refreshes + grace
AGENT_WAIT_GRACE_SECONDS = 60


# ---------------------------------------------------------------------------
# Reviewer Concurrency Cap
# ---------------------------------------------------------------------------

# The pipeline's own override, honored on both hosts. On Codex it is the only
# way to opt into dispatch waves, because Codex does not expose its
# agent-thread limit through the environment.
REVIEWER_CAP_ENV = "PIRATEGOAT_MAX_CONCURRENT_REVIEWERS"
# Claude Code's concurrent-subagent limit, and its default when unset.
CLAUDE_SUBAGENT_CAP_ENV = "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"
DEFAULT_CLAUDE_SUBAGENT_CAP = 20

# Where a resolved cap came from. Recorded in the plan's `dispatch_waves`
# record and in the run manifest; `dispatch_status.validate_dispatch_waves()`
# accepts exactly this set.
CAP_SOURCE_PIRATEGOAT_ENV = "pirategoat_env"
CAP_SOURCE_CLAUDE_ENV = "claude_env"
CAP_SOURCE_CLAUDE_DEFAULT = "claude_default"
CAP_SOURCE_UNBOUNDED = "unbounded"
CAP_SOURCES = frozenset({
    CAP_SOURCE_PIRATEGOAT_ENV,
    CAP_SOURCE_CLAUDE_ENV,
    CAP_SOURCE_CLAUDE_DEFAULT,
    CAP_SOURCE_UNBOUNDED,
})


def _positive_int(raw):
    """Parse an env value as a positive decimal int, or return None."""
    text = str(raw).strip()
    if not re.fullmatch(r"[0-9]+", text):
        return None
    value = int(text)
    return value if value > 0 else None


def resolve_reviewer_cap(host, env):
    """Resolve how many reviewers step 6 may launch at once.

    Returns ``(cap, source, invalid_env)``: ``cap`` is a positive int, or
    None for unbounded; ``source`` is one of ``CAP_SOURCES``; ``invalid_env``
    names every consulted variable that was set to something other than a
    positive decimal integer (``"0"``, ``"-3"``, ``"abc"``, ``""``), so the
    step-6 situation can say which setting was ignored.

    Order: ``PIRATEGOAT_MAX_CONCURRENT_REVIEWERS`` on either host; then, on
    Claude Code, ``CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`` and finally the
    default of 20; on Codex, unbounded. An invalid value falls through to the
    next source. ``host`` is normalized like ``_host()`` (unknown means
    Claude). Pure: ``env`` is any mapping, normally ``os.environ``.
    """
    invalid = []

    def _read(name):
        if name not in env:
            return None
        value = _positive_int(env[name])
        if value is None:
            invalid.append(name)
        return value

    cap = _read(REVIEWER_CAP_ENV)
    if cap is not None:
        return cap, CAP_SOURCE_PIRATEGOAT_ENV, invalid
    if _host({"host": host}) == HOST_CODEX:
        return None, CAP_SOURCE_UNBOUNDED, invalid
    cap = _read(CLAUDE_SUBAGENT_CAP_ENV)
    if cap is not None:
        return cap, CAP_SOURCE_CLAUDE_ENV, invalid
    return DEFAULT_CLAUDE_SUBAGENT_CAP, CAP_SOURCE_CLAUDE_DEFAULT, invalid


def _git_output(*args):
    """Return one Git identity value, or an empty string when unavailable."""
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
    except Exception:
        return ""

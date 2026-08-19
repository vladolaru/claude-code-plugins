#!/usr/bin/env python3
"""
Unified Review Pipeline — curated-context-pipeline for code reviews.

PIPELINE GOAL: Deliver a complete review of code changes that is comprehensive
in its analysis, contextual in its focus, accurate in its findings, and actionable
in its recommendations — maintaining a high quality bar for codebases so they can
deliver great business results and awesome user experiences.

This directly executable facade owns conditions, routing, state I/O, output
formatting, telemetry/Git identity, and the CLI. Mode (pr|full|incremental) and
data-driven conditions determine which steps run. Shared vocabulary lives in
pipeline_contract.py, pure conversational briefings in briefings.py, and
side-effecting per-step work in orchestration.py. Three command .md files are
thin wrappers calling this facade with --mode flags.

Split file-based state:
  - run-config.json:     Caller config (mode, pr_number, interactive, output_instructions).
                         Set before step 1 (or by the script at step 1 from CLI args).
                         Read-only during the run.
  - pipeline-state.json: Execution state. Owned exclusively by the script.
                         The LLM never reads or writes it.

Zero external dependencies (stdlib only).
"""

import argparse
import glob as glob_mod
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from .briefings import (
        _PIPELINE_MISSION,
        _PHASE_TRANSITIONS,
        get_step_guidance,
        _step_1_parse_input,
        _step_2_repo_setup,
        _markdown_code_span,
        _format_pr_metadata,
        _format_reviews_summary,
        _format_size,
        _format_staleness,
        _format_domain_counts,
        _format_linked_issues,
        _change_purpose_handoff,
        _dependency_refresh_briefing,
        _step_3_gather_context,
        _step_4_fetch_issues,
        _step_5_dispatch_plan,
        _step_6_dispatch_agents,
        _step_7_save_baseline,
        _step_8_reconcile,
        _DEFAULT_OUTPUT_INSTRUCTIONS_PR,
        _DEFAULT_OUTPUT_INSTRUCTIONS_BRANCH,
        _step_9_review_report,
        _step_10_decision_critic,
        _step_11_present_results,
        _step_12_cleanup,
    )
    from .orchestration import (
        _preserve_initial_dispatch_plan,
        _load_dispatch_plan,
        _run_subprocess,
        _detect_dependency_refresh_state,
        _orchestrate_step_2,
        _orchestrate_step_3,
        _orchestrate_step_5,
        _orchestrate_step_6,
        _orchestrate_step_7,
        _orchestrate_step_8,
        _orchestrate_step_9,
        _orchestrate_step_10,
        _orchestrate_step_11,
        _orchestrate_step,
    )
    from .pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        AGENTS_DIR,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        HOST_CLAUDE,
        HOST_CODEX,
        PLUGIN_ROOT,
        SCRIPTS_DIR,
        STEP_SEQUENCE,
        SUPPORTED_HOSTS,
        _STEP_MAP,
        _agent_definition_path,
        _codex_agent_instruction,
        _codex_task_name,
        _git_output,
        _host,
        _stop_operation,
    )
    from .dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        SKIPPED_QUICK_MODE,
        validate_dispatch_plan_agents,
    )
    from .dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        SKIP_REASON_DIRTY_WORKTREE,
        SKIP_REASON_WORKTREE_STATUS_FAILED,
        detect_dependency_refresh,
        verify_dependency_refresh,
    )
    from .user_settings import load_user_settings, refresh_dependencies_default
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.briefings import (
        _PIPELINE_MISSION,
        _PHASE_TRANSITIONS,
        get_step_guidance,
        _step_1_parse_input,
        _step_2_repo_setup,
        _markdown_code_span,
        _format_pr_metadata,
        _format_reviews_summary,
        _format_size,
        _format_staleness,
        _format_domain_counts,
        _format_linked_issues,
        _change_purpose_handoff,
        _dependency_refresh_briefing,
        _step_3_gather_context,
        _step_4_fetch_issues,
        _step_5_dispatch_plan,
        _step_6_dispatch_agents,
        _step_7_save_baseline,
        _step_8_reconcile,
        _DEFAULT_OUTPUT_INSTRUCTIONS_PR,
        _DEFAULT_OUTPUT_INSTRUCTIONS_BRANCH,
        _step_9_review_report,
        _step_10_decision_critic,
        _step_11_present_results,
        _step_12_cleanup,
    )
    from review.orchestration import (
        _preserve_initial_dispatch_plan,
        _load_dispatch_plan,
        _run_subprocess,
        _detect_dependency_refresh_state,
        _orchestrate_step_2,
        _orchestrate_step_3,
        _orchestrate_step_5,
        _orchestrate_step_6,
        _orchestrate_step_7,
        _orchestrate_step_8,
        _orchestrate_step_9,
        _orchestrate_step_10,
        _orchestrate_step_11,
        _orchestrate_step,
    )
    from review.pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        AGENTS_DIR,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        HOST_CLAUDE,
        HOST_CODEX,
        PLUGIN_ROOT,
        SCRIPTS_DIR,
        STEP_SEQUENCE,
        SUPPORTED_HOSTS,
        _STEP_MAP,
        _agent_definition_path,
        _codex_agent_instruction,
        _codex_task_name,
        _git_output,
        _host,
        _stop_operation,
    )
    from review.dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        SKIPPED_QUICK_MODE,
        validate_dispatch_plan_agents,
    )
    from review.dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        SKIP_REASON_DIRTY_WORKTREE,
        SKIP_REASON_WORKTREE_STATUS_FAILED,
        detect_dependency_refresh,
        verify_dependency_refresh,
    )
    from review.user_settings import (
        load_user_settings,
        refresh_dependencies_default,
    )

# Artifacts to clear at step 1 (stale from previous runs)
_STALE_ARTIFACTS = [
    "pipeline-state.json",
    ".telemetry-log-path",
    "dispatch-plan.json",
    "dispatch-plan.initial.json",
    "*-review.json",
    "*-review.md",
    "*-scope-summary*.json",
    "*-deferred-files.json",
    "*-advisory-entitlement.json",
    "*.started",
    "reconciliation-context.json",
    "reconciliation-context.md",
    "critic-context.md",
    "review-findings.json",
    "review-findings.md",
    "review-report.md",
    "review-verdict.json",
    "pipeline-result.json",
    "decision-critic-findings.md",
    "decision-critic-verdict.json",
    "change-purpose.md",
    "scoped-diff.patch",
    "*-scoped-diff.patch",
    "dependency-refresh.json",
    "dependency-refresh-verification.json",
    # Listed by exact name, not swept by a glob: `glob` never matches a
    # leading dot, so a `*.json` pattern would silently skip the baseline
    # (same reason `.telemetry-log-path` is spelled out above). A stale
    # baseline surviving into the next run is what would let a failed
    # capture read as a successful one — and, before the repo-identity
    # gate, could have authorized a sweep in a repo this run never
    # measured.
    ".worktree-baseline.json",
    "worktree-hygiene.json",
    # Same per-run rule: a surviving snapshot would let a rerun in this
    # output dir publish a previous run's token cost as its own.
    "usage-snapshot.json",
]

# Files to preserve across runs
_PRESERVED_FILES = {
    "run-config.json",
    ".branch-review-baseline.json",
}


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------

def _eval_condition(condition, mode, config, state, context):
    """Evaluate a step condition. Returns True if step should run."""
    if condition == "always":
        return True

    if condition == "needs_workspace_setup":
        # PR mode + interactive + no pre-computed merge_base
        if mode != "pr":
            return False
        if not config.get("interactive", True):
            return False
        git = context.get("git", {})
        return not git.get("merge_base")

    if condition == "has_unfetched_issues":
        return state.get("resolved_params", {}).get("has_unfetched_issues", False)

    if condition == "has_workspace_state_interactive":
        ws = state.get("workspace", {})
        has_branch = ws.get("original_branch") is not None
        is_interactive = config.get("interactive", True)
        return has_branch and is_interactive

    return False


# ---------------------------------------------------------------------------
# Step Routing
# ---------------------------------------------------------------------------

def get_active_steps(mode, config, state, context):
    """Return set of active step numbers for this mode/config/state/context."""
    active = set()
    for step_def in STEP_SEQUENCE:
        if _eval_condition(step_def["condition"], mode, config, state, context):
            active.add(step_def["step"])
    return active


def compute_next_step(current_step, active_steps):
    """Compute the next step after current_step.

    Returns dict with 'step', 'title', and optional 'skip_reason',
    or None if current_step is the last active step.
    """
    # Find next active step after current
    candidates = sorted(s for s in active_steps if s > current_step)
    if not candidates:
        return None

    next_num = candidates[0]
    step_def = _STEP_MAP[next_num]

    # Compute skip reason if steps were skipped
    skip_reason = None
    skipped = [s for s in range(current_step + 1, next_num) if s not in active_steps]
    if skipped:
        skipped_titles = [_STEP_MAP[s]["title"] for s in skipped]
        skip_reason = f"Skipped: {', '.join(f'Step {s} ({t})' for s, t in zip(skipped, skipped_titles))}"

    return {
        "step": next_num,
        "title": step_def["title"],
        "skip_reason": skip_reason,
    }


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

_DEFAULT_STATE = {
    "run_id": "",
    "completed_steps": [],
    "skipped_steps": [],
    "resolved_params": {
        "has_unfetched_issues": False,
    },
    "workspace": {
        "original_branch": None,
        "stash_ref": None,
    },
    "agents": {
        "dispatched": [],
        "completed": [],
        "failed": [],
    },
    "reviewer_markdown": {
        "ran": False,
        "written": 0,
        "expected": 0,
        "status": "not_run",
    },
    "verdict": None,
}

_DEFAULT_CONFIG = {}


def read_state(output_dir):
    """Read pipeline-state.json, return default if missing or corrupted."""
    path = os.path.join(output_dir, "pipeline-state.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_DEFAULT_STATE))


def write_state(output_dir, state):
    """Write pipeline-state.json."""
    path = os.path.join(output_dir, "pipeline-state.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def read_config(output_dir):
    """Read run-config.json, return empty dict if missing or corrupted."""
    path = os.path.join(output_dir, "run-config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)


def write_config(output_dir, config):
    """Write run-config.json."""
    path = os.path.join(output_dir, "run-config.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _reset_interactive_review_context(output_dir):
    """Atomically replace prior-run context with the current run seed."""
    context = {"output": {"directory": output_dir}}
    path = os.path.join(output_dir, "review-context.json")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=output_dir,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(context, temp_file, indent=2)
            temp_file.flush()
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return context


def read_review_context(output_dir):
    """Read preserved review-context.json, or return an empty dict."""
    path = os.path.join(output_dir, "review-context.json")
    try:
        with open(path) as f:
            context = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    # A valid-JSON array/scalar would crash every context.get() consumer —
    # degrade to the same empty fallback as malformed JSON.
    return context if isinstance(context, dict) else {}


def resolve_params(output_dir, cli_mode=None, cli_pr_number=None,
                   cli_interactive=None, cli_output_instructions=None,
                   cli_git_range=None):
    """Resolve parameters: run-config.json wins over CLI args."""
    config = read_config(output_dir)
    # Config values take precedence; CLI fills in missing fields
    resolved = {}
    resolved["mode"] = config.get("mode") or cli_mode
    resolved["pr_number"] = config.get("pr_number") or cli_pr_number
    if "interactive" in config:
        resolved["interactive"] = config["interactive"]
    elif cli_interactive is not None:
        resolved["interactive"] = cli_interactive
    else:
        resolved["interactive"] = True
    if "output_instructions" in config:
        resolved["output_instructions"] = config["output_instructions"]
    elif cli_output_instructions:
        resolved["output_instructions"] = cli_output_instructions
    if config.get("git_range") or cli_git_range:
        resolved["git_range"] = config.get("git_range") or cli_git_range
    return resolved


# ---------------------------------------------------------------------------
# Stale Artifact Cleanup
# ---------------------------------------------------------------------------

def clean_stale_artifacts(output_dir):
    """Remove stale run artifacts, preserving run-config.json and .branch-review-baseline.json."""
    for pattern in _STALE_ARTIFACTS:
        if "*" in pattern:
            for filepath in glob_mod.glob(os.path.join(output_dir, pattern)):
                basename = os.path.basename(filepath)
                if basename not in _PRESERVED_FILES:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
        else:
            filepath = os.path.join(output_dir, pattern)
            basename = os.path.basename(filepath)
            if basename not in _PRESERVED_FILES and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

def format_output(step, guidance):
    """Format guidance into curated-context-pipeline output."""
    lines = []

    # Header
    phase = guidance["phase"]
    title = guidance["title"]
    lines.append(f"{'═' * 60}")
    lines.append(f"REVIEW PIPELINE Step {step} — {phase}: {title}")
    lines.append(f"{'═' * 60}")
    lines.append("")

    # Skip explanation (if steps were skipped to get here)
    skip_reason = guidance.get("skip_reason")
    if skip_reason:
        lines.append(f"ℹ️  {skip_reason}")
        lines.append("")

    # Situation
    if guidance.get("situation"):
        lines.append("## SITUATION")
        lines.append("")
        for item in guidance["situation"]:
            lines.append(item)
        lines.append("")

    # Actions
    if guidance.get("actions"):
        lines.append("## ACTIONS")
        lines.append("")
        for item in guidance["actions"]:
            lines.append(item)
        lines.append("")

    # Handoff
    if guidance.get("handoff"):
        lines.append("## HANDOFF — Required before proceeding")
        lines.append("")
        for item in guidance["handoff"]:
            lines.append(f"- {item}")
        lines.append("")

    # Next step pointer or completion
    next_step = guidance.get("next_step")
    if guidance.get("blocks_progress"):
        lines.append(f"{'─' * 60}")
        lines.append("⏸️  PIPELINE WAITING")
        lines.append("")
        lines.append("Complete the actions above, then re-run this step.")
        lines.append(f"Run: python3 {SCRIPTS_DIR / 'pipeline.py'} --step {step} --output-dir <OUTPUT_DIR>")
    elif next_step:
        lines.append(f"{'─' * 60}")
        ns = next_step
        lines.append(f"➡️  Next: Step {ns['step']} — {ns['title']}")
        if ns.get("skip_reason"):
            lines.append(f"    ({ns['skip_reason']})")
        lines.append("")
        lines.append(f"Run: python3 {SCRIPTS_DIR / 'pipeline.py'} --step {ns['step']} --output-dir <OUTPUT_DIR>")
    else:
        lines.append(f"{'─' * 60}")
        lines.append("✅ PIPELINE COMPLETE")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telemetry Integration (best-effort)
# ---------------------------------------------------------------------------

def _init_telemetry(output_dir, log_dir=None):
    """Import and initialize ReviewTelemetry. Returns None on failure."""
    try:
        import importlib.util
        telemetry_path = SCRIPTS_DIR / "telemetry.py"
        spec = importlib.util.spec_from_file_location("review_telemetry", telemetry_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Check for env override
        env_log_dir = os.environ.get("PIRATEGOAT_TELEMETRY_LOG_DIR")
        return mod.ReviewTelemetry(output_dir, log_dir=env_log_dir or log_dir)
    except Exception:
        return None


_SEMVER_PATTERN = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
_SEMVER_ROOT_RE = re.compile(rf"^{_SEMVER_PATTERN}$")
_CHANGELOG_VERSION_RE = re.compile(rf"^## \[({_SEMVER_PATTERN})\]", re.MULTILINE)
# Full SHA-1 (40 hex) or SHA-256 (64 hex) object name.
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _detect_plugin_version(plugin_root=None):
    """Return the installed or source-checkout plugin version, best-effort."""
    try:
        root = Path(plugin_root) if plugin_root is not None else SCRIPTS_DIR.parent.parent
        if _SEMVER_ROOT_RE.fullmatch(root.name):
            return root.name

        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        match = _CHANGELOG_VERSION_RE.search(changelog)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _resolve_git_identity(git_range, base_sha="", head_sha=""):
    """Resolve requested range endpoints without mutating Git.

    Omitted endpoints around ``..`` or ``...`` default to ``HEAD``. For a
    three-dot range, ``base_sha`` is the resolved left endpoint, not the Git
    merge base; later context or manifest collection can record that value.
    """
    requested_range = git_range if isinstance(git_range, str) else ""
    base_ref = ""
    head_ref = ""
    has_range_operator = False
    if "..." in requested_range:
        base_ref, head_ref = requested_range.split("...", 1)
        has_range_operator = True
    elif ".." in requested_range:
        base_ref, head_ref = requested_range.split("..", 1)
        has_range_operator = True

    base_ref = base_ref.strip()
    head_ref = head_ref.strip()
    if has_range_operator:
        base_ref = base_ref or "HEAD"
        head_ref = head_ref or "HEAD"

    # Supplied context values may be symbolic (an explicit range like
    # "main..HEAD" stores "main" as the context merge_base). The durable
    # manifest must record COMMIT identity: ^{commit} both resolves refs and
    # peels annotated tags, whose plain rev-parse would return the tag
    # OBJECT id — even a full-hex supplied value can be a tag object.
    def resolve_endpoint(supplied, ref):
        for candidate in (supplied if isinstance(supplied, str) else "", ref):
            if not candidate:
                continue
            peeled = _git_output(
                "rev-parse", "--verify", f"{candidate}^{{commit}}"
            )
            if peeled:
                return peeled
            if _FULL_SHA_RE.fullmatch(candidate):
                # Git unavailable — an already-full object id is the best
                # obtainable identity.
                return candidate
        return ""

    return (
        requested_range,
        resolve_endpoint(base_sha, base_ref),
        resolve_endpoint(head_sha, head_ref or "HEAD"),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_refresh_dependencies(cli_value):
    """Resolve the trusted-branch refresh opt-in for this step-1 call.

    An explicit --refresh-deps / --no-refresh-deps wins; an omitted flag
    falls back to the requester's machine-local trust declaration
    (~/.config/pirategoat/config.json). The interactive-only hard-off in
    main() still runs after this and remains authoritative for bot runs.
    """
    if cli_value is not None:
        return cli_value
    try:
        return refresh_dependencies_default(load_user_settings())
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Unified review pipeline")
    parser.add_argument("--step", type=int, required=True, help="Step number (1-12)")
    parser.add_argument("--mode", choices=["pr", "full", "incremental"],
                        help="Review mode")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--pr-number", help="PR number (PR mode)")
    parser.add_argument("--session-id", help="Claude session ID for telemetry correlation")
    parser.add_argument("--interactive", type=lambda x: x.lower() in ("true", "1", "yes"),
                        default=None, help="Interactive mode (default: true)")
    parser.add_argument("--output-instructions", help="Custom output instructions")
    parser.add_argument("--git-range", help="Explicit git range")
    parser.add_argument("--original-branch", help="Branch to restore on cleanup")
    parser.add_argument("--stash-ref", help="Stash ref to restore on cleanup")
    parser.add_argument("--quick", action="store_true", default=False,
                        help="Quick review mode: fewer agents, conditional critic skip")
    parser.add_argument("--refresh-deps", action=argparse.BooleanOptionalAction,
                        default=None,
                        help="Trusted-branch mode: authorize the orchestrator "
                             "to refresh installed dependencies in the "
                             "worktree with frozen-mode installs "
                             "(interactive runs only). Omitted, the "
                             "requester's machine-local config default "
                             "applies (~/.config/pirategoat/config.json)")
    parser.add_argument("--host", choices=SUPPORTED_HOSTS, default=None,
                        help="Orchestration host (default on first call: claude)")

    args = parser.parse_args()
    output_dir = args.output_dir
    step = args.step

    # Ensure output dir exists
    os.makedirs(output_dir, exist_ok=True)
    context = read_review_context(output_dir)

    # --- Step 1: Special handling (seed config, clean artifacts) ---
    if step == 1:
        # Clean stale artifacts first
        clean_stale_artifacts(output_dir)

        # Resolve mode: config wins, then CLI, then error
        existing_config = read_config(output_dir)
        mode = existing_config.get("mode") or args.mode
        if not mode:
            print("ERROR: --mode is required on the first call", file=sys.stderr)
            sys.exit(2)

        # Write/update run-config.json (seed from CLI on first call)
        if not existing_config.get("mode"):
            config = {
                "mode": mode,
                "host": args.host or HOST_CLAUDE,
            }
            if args.pr_number:
                config["pr_number"] = args.pr_number
            if args.interactive is not None:
                config["interactive"] = args.interactive
            else:
                config["interactive"] = True
            if args.output_instructions:
                config["output_instructions"] = args.output_instructions
            if args.git_range:
                config["git_range"] = args.git_range
            if args.session_id is not None:
                config["session_id"] = args.session_id
            config["quick"] = args.quick
            config["refresh_dependencies"] = \
                _resolve_refresh_dependencies(args.refresh_deps)
            write_config(output_dir, config)
        else:
            config = existing_config
            config_changed = False
            if "host" not in config:
                config["host"] = args.host or HOST_CLAUDE
                config_changed = True
            elif args.host is not None and config.get("host") != args.host:
                config["host"] = args.host
                config_changed = True
            # On interactive rerun, sync --quick from CLI into config.
            # Without this, a quick→normal rerun stays in quick mode,
            # and a normal→quick rerun was already handled.
            # In bot mode (interactive: false), the bot pre-writes the
            # correct quick value in run-config.json and subsequent steps
            # may not pass --quick on the CLI (especially with custom
            # prompt overrides), so we must not overwrite the bot's value.
            if config.get("interactive", True) and config.get("quick") != args.quick:
                config["quick"] = args.quick
                config_changed = True
            # refresh_dependencies follows the same interactive rerun
            # semantics as quick: the CLI is authoritative, with an omitted
            # flag resolving to the requester's machine-local default.
            effective_refresh = _resolve_refresh_dependencies(args.refresh_deps)
            if config.get("interactive", True) and \
                    config.get("refresh_dependencies", False) != effective_refresh:
                config["refresh_dependencies"] = effective_refresh
                config_changed = True
            if config_changed:
                write_config(output_dir, config)
            # Session identity follows the same interactive/bot split as
            # quick: interactive reruns reuse output dirs and run-config.json
            # survives cleanup, so the CLI is authoritative INCLUDING
            # absence — an omitted --session-id means this run's session is
            # unknown, and retaining the previous run's ID would correlate
            # telemetry with the old Claude transcript. Bot runs pre-seed
            # the ID in run-config.json and may omit the flag on reruns.
            if config.get("interactive", True):
                cli_session = args.session_id or ""
                if config.get("session_id", "") != cli_session:
                    if cli_session:
                        config["session_id"] = cli_session
                    else:
                        config.pop("session_id", None)
                    write_config(output_dir, config)
            elif (
                args.session_id is not None
                and config.get("session_id") != args.session_id
            ):
                config["session_id"] = args.session_id
                write_config(output_dir, config)

        # Trusted-branch dependency refresh executes reviewed-branch code
        # (package managers run configuration as code), so it is
        # interactive-only: a bot reviewing third-party PRs must never
        # inherit it, whether from the CLI or a pre-seeded run-config.json.
        if not config.get("interactive", True) and \
                config.get("refresh_dependencies"):
            config["refresh_dependencies"] = False
            write_config(output_dir, config)
            print("WARNING: --refresh-deps / refresh_dependencies is "
                  "interactive-only; disabled for this non-interactive run.",
                  file=sys.stderr)

        # Interactive output directories may be reused, so prior-run context
        # cannot remain authoritative until step 3 gathers it afresh. Bot runs
        # are non-interactive and retain their precomputed context contract.
        if config.get("interactive", True):
            context = _reset_interactive_review_context(output_dir)

        # Initialize fresh pipeline state
        state = json.loads(json.dumps(_DEFAULT_STATE))
        now = datetime.now(timezone.utc)
        identifier = config.get("pr_number", "branch")
        state["run_id"] = (
            f"{now.strftime('%Y%m%dT%H%M%S')}-{mode}-{identifier}-"
            f"{uuid.uuid4().hex[:8]}"
        )

        # Persist workspace params
        if args.original_branch:
            state["workspace"]["original_branch"] = args.original_branch
        if args.stash_ref:
            state["workspace"]["stash_ref"] = args.stash_ref

        write_state(output_dir, state)

        # Telemetry: start
        telemetry = _init_telemetry(output_dir)
        if telemetry:
            try:
                pr_number = config.get("pr_number", "")
                bot_mode = not config.get("interactive", True)
                quick_mode = config.get("quick", False)
                repo_path = _git_output("rev-parse", "--show-toplevel")
                # Identifier: PR number for pr mode, branch name otherwise
                identifier = pr_number
                if not identifier:
                    identifier = _git_output("branch", "--show-current")
                git_context = (
                    context.get("git", {})
                    if not config.get("interactive", True)
                    else {}
                )
                config_git_range = config.get("git_range", "")
                context_git_range = git_context.get("git_range", "")
                git_range = config_git_range or context_git_range
                context_matches_range = (
                    not config_git_range or config_git_range == context_git_range
                )
                context_base_sha = (
                    git_context.get("merge_base", "") if context_matches_range else ""
                )
                context_head_sha = (
                    git_context.get("head_sha", "") if context_matches_range else ""
                )
                git_range, base_sha, head_sha = _resolve_git_identity(
                    git_range, base_sha=context_base_sha,
                    head_sha=context_head_sha,
                )
                telemetry.start(pr_number=pr_number, total_steps=12,
                                bot_mode=bot_mode, quick_mode=quick_mode,
                                mode=mode, repo_path=repo_path,
                                identifier=identifier,
                                run_id=state["run_id"],
                                session_id=config.get("session_id", ""),
                                plugin_version=_detect_plugin_version(),
                                git_range=git_range, base_sha=base_sha,
                                head_sha=head_sha)
            except Exception:
                pass

    else:
        # Steps 2+: read existing config and state
        config = read_config(output_dir)
        mode = config.get("mode") or args.mode
        if not mode:
            print("ERROR: No mode found in run-config.json and --mode not provided",
                  file=sys.stderr)
            sys.exit(2)

        state = read_state(output_dir)

        # Persist workspace params if provided
        if args.original_branch:
            state["workspace"]["original_branch"] = args.original_branch
        if args.stash_ref:
            state["workspace"]["stash_ref"] = args.stash_ref

        # Telemetry: log step (deferred until after orchestration, see below)

    # Validate step number
    if step not in _STEP_MAP:
        print(f"ERROR: Invalid step {step}. Valid steps: 1-12", file=sys.stderr)
        sys.exit(1)

    # --- Step-specific orchestration ---
    # A dispatch plan that fails validation is operator-actionable (step 5 invites
    # hand-editing statuses), so surface it as a clean CLI error instead of a
    # traceback. Matches agents_status.py, the other consumer of that contract.
    try:
        context = _orchestrate_step(step, mode, config, state, context, output_dir)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    # Telemetry: log step (after orchestration so decisions are available)
    if step > 1:
        telemetry = _init_telemetry(output_dir)
        if telemetry:
            try:
                step_def = _STEP_MAP.get(step, {})
                bot_mode = not config.get("interactive", True)
                decisions = state.get("step_decisions", {}).get(str(step))
                telemetry.log_step(
                    step=step, phase=step_def.get("phase", ""),
                    title=step_def.get("title", ""),
                    bot_mode=bot_mode,
                    decisions=decisions,
                )
            except Exception:
                pass

    # Check for hard error: non-interactive PR without pre-computed context
    if mode == "pr" and not config.get("interactive", True):
        git_ctx = context.get("git", {})
        if not git_ctx.get("merge_base") and step <= 2:
            print("PIPELINE STOPPED: Non-interactive PR mode requires pre-computed "
                  "review-context.json with a valid merge_base.", file=sys.stderr)
            sys.exit(1)

    # --- Get guidance ---
    guidance = get_step_guidance(step, mode, state, context, config=config,
                                output_dir=output_dir)
    if guidance is None:
        print(f"ERROR: No guidance for step {step}", file=sys.stderr)
        sys.exit(1)

    blocks_progress = guidance.get("blocks_progress", False)

    # --- Update state ---
    if not blocks_progress and step not in state.get("completed_steps", []):
        state.setdefault("completed_steps", []).append(step)
    write_state(output_dir, state)

    # --- Compute routing AFTER orchestration/guidance (state may have changed) ---
    active = get_active_steps(mode, config, state, context)

    # Add next step info
    next_info = None if blocks_progress else compute_next_step(step, active)
    guidance["next_step"] = next_info
    if next_info:
        guidance["skip_reason"] = next_info.get("skip_reason")
    else:
        guidance["skip_reason"] = None

    # Record skip decisions where they are made — dispatch-plan symmetry:
    # a step the router passes over must say so in durable state with its
    # gating condition, not only in orchestrator-facing prose. Trailing
    # skips (steps after the last active one) are recorded when next_info
    # is None, right before finalize.
    #
    # This is a second write_state, and that is a choice rather than a
    # constraint: nothing between the two writes touches disk or can
    # raise (get_active_steps and compute_next_step are pure functions of
    # their in-memory arguments, and _eval_condition is total), so the
    # earlier write could equally be moved down here. Two are kept because
    # the extra cost is negligible — a small file, written only when
    # something new was recorded — and because a crash between them fails
    # in the safe direction: the ledger is then absent or short, which
    # reads as unmeasured or partial, never as a wrong measurement.
    if not blocks_progress:
        upper = next_info["step"] if next_info else max(_STEP_MAP) + 1
        already = {
            entry.get("step")
            for entry in state.get("skipped_steps", [])
            if isinstance(entry, dict)
        }
        newly_skipped = []
        for skipped_step in range(step + 1, upper):
            if skipped_step in active or skipped_step in already:
                continue
            # Step numbers are contiguous today, so the gap can only hold
            # defined steps; resolving through .get() keeps a future
            # sparse or renumbered STEP_SEQUENCE from turning a routing
            # gap into a KeyError mid-run.
            step_def = _STEP_MAP.get(skipped_step)
            if step_def is None:
                continue
            newly_skipped.append({
                "step": skipped_step,
                "title": step_def["title"],
                "condition": step_def["condition"],
            })
        if newly_skipped:
            state.setdefault("skipped_steps", []).extend(newly_skipped)
            write_state(output_dir, state)

    # Telemetry: finalize at last active step
    if next_info is None and not blocks_progress and telemetry:
        try:
            step_def = _STEP_MAP.get(step, {})
            bot_mode = not config.get("interactive", True)
            telemetry.finalize(
                step=step, phase=step_def.get("phase", ""),
                title=step_def.get("title", ""),
                bot_mode=bot_mode,
            )
        except Exception:
            pass

    # --- Format and output ---
    output = format_output(step, guidance)
    print(output)


if __name__ == "__main__":
    main()

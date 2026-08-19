"""Side-effecting step orchestration for the review pipeline."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        SCRIPTS_DIR,
        _git_output,
        _host,
    )
    from .dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from .dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        detect_dependency_refresh,
        verify_dependency_refresh,
    )
    from . import critic_adjustments
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        SCRIPTS_DIR,
        _git_output,
        _host,
    )
    from review.dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from review.dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        detect_dependency_refresh,
        verify_dependency_refresh,
    )
    from review import critic_adjustments


# Reserved marker for pipeline-created probe files. Nothing user-owned
# may carry it, which is what makes the step-11 residue sweep a safe,
# targeted delete instead of a tree-wide git reset/clean (forbidden:
# the reviewed repo is the user's live tree and may hold uncommitted work).
PROBE_MARKER = "pirategoat-probe"

# One spelling of the status arguments for both the step-3 snapshot and the
# step-11 comparison, so the two can never drift into reporting a format
# difference as a worktree change. `--untracked-files=all` is load-bearing:
# plain porcelain collapses an untracked directory into a single "?? dir/"
# entry without recursing, which would both coarsen the comparison and hide
# a probe file created inside a new directory from the sweep. Porcelain
# reports every path relative to the repository root regardless of where it
# is invoked, which is why both halves pin the root explicitly below.
_GIT_STATUS_ARGS = ["status", "--porcelain", "--untracked-files=all"]

# ---------------------------------------------------------------------------
# Dispatch Plan Persistence
# ---------------------------------------------------------------------------

def _preserve_initial_dispatch_plan(output_dir, plan):
    """Atomically preserve the planner baseline without blocking the review.

    Any prior baseline is removed first so a failed measurement write cannot
    make an older plan look like the current run's deterministic output.
    """
    initial_path = os.path.join(output_dir, "dispatch-plan.initial.json")
    temp_path = None
    try:
        try:
            os.remove(initial_path)
        except FileNotFoundError:
            pass

        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=output_dir,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(plan, temp_file, indent=2, sort_keys=True)
            temp_file.flush()
        os.replace(temp_path, initial_path)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(initial_path)
        except OSError:
            pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _load_dispatch_plan(plan_path):
    """Load one dispatch plan and validate its agent decisions."""
    with open(plan_path) as plan_file:
        plan = json.load(plan_file)
    if not isinstance(plan, dict):
        raise ValueError(
            f"Dispatch plan at {plan_path} must be a JSON object, got {plan!r}"
        )
    validate_dispatch_plan_agents(plan.get("agents"))
    return plan


# ---------------------------------------------------------------------------
# Subprocess Helper
# ---------------------------------------------------------------------------

def _run_subprocess(cmd, cwd=None, timeout=60):
    """Run a subprocess and return (stdout, success). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip(), True
        print(f"WARNING: {cmd[0]} exited {r.returncode}: {r.stderr[:200]}", file=sys.stderr)
        return r.stdout.strip(), False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"WARNING: {cmd[0]} failed: {e}", file=sys.stderr)
        return "", False


def _materialize_reviewer_markdown(output_dir: str, output_builder_path: str) -> list:
    """Render per-reviewer Markdown from the settled JSONs.

    Loads the output builder by exact adjacent path — the same contract the
    telemetry and dispatch-status loaders use — so a long-lived process
    can never render with a foreign checkout's semantics.
    """
    spec = importlib.util.spec_from_file_location(
        "_pirategoat_review_output", output_builder_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.materialize_markdown(output_dir)


# ---------------------------------------------------------------------------
# Step Orchestration (side effects — subprocesses, file I/O)
# ---------------------------------------------------------------------------

def _detect_dependency_refresh_state(context):
    """Run stale-dependency detection against the reviewed repo root.

    Failure is honest, never silent: a missing repo root or a crashed
    detection reports detection_failed instead of an empty clean result.
    """
    try:
        repo_root = _git_output("rev-parse", "--show-toplevel")
        if not repo_root:
            return {"signals": [], "detection_failed": True}
        changed = context.get("git", {}).get("changed_files") or []
        result = detect_dependency_refresh(repo_root, changed)
        if not isinstance(result, dict) or "signals" not in result:
            return {"signals": [], "detection_failed": True}
        return result
    except Exception:
        return {"signals": [], "detection_failed": True}


def _orchestrate_step_2(mode, config, state, context, output_dir):
    # Run workspace_setup.py to stash, record branch, checkout PR
    pr_number = config.get("pr_number", "")
    if pr_number:
        setup_cmd = [
            sys.executable, str(SCRIPTS_DIR / "workspace_setup.py"),
            "--pr-number", str(pr_number),
        ]
        stdout, ok = _run_subprocess(setup_cmd, timeout=60)
        if ok and stdout:
            try:
                ws_result = json.loads(stdout)
                state["workspace"]["original_branch"] = ws_result.get("original_branch")
                state["workspace"]["stash_ref"] = ws_result.get("stash_ref")
                state["workspace_setup_result"] = ws_result
            except (json.JSONDecodeError, KeyError):
                state["workspace_setup_result"] = {"error": "Failed to parse script output"}
        else:
            state["workspace_setup_result"] = {
                "error": "workspace_setup.py failed or produced no output",
                "checkout_ok": False,
            }

    return context


# ---------------------------------------------------------------------------
# Worktree Hygiene
# ---------------------------------------------------------------------------

def _resolve_repo_root():
    """Absolute, symlink-resolved root of the repo containing CWD, or None.

    Repo identity is what makes the step-11 sweep safe: a baseline records
    the repo it measured and the sweep refuses to delete anywhere else.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.strip()
    if not root:
        return None
    try:
        return os.path.realpath(root)
    except OSError:
        return None


def _git_status_lines(repo_root):
    """Porcelain status of `repo_root`, or None when git could not answer.

    Pinned to an explicit root with `git -C`, the same way
    dependency_refresh.py's `_tracked_worktree_status()` pins its own probe,
    so the measurement never silently describes whatever repo the process
    happened to be standing in.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root] + _GIT_STATUS_ARGS,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _capture_worktree_baseline(output_dir):
    """Snapshot the reviewed worktree's git status for end-of-run hygiene.

    The snapshot records the repo root it measured, because step 11 will not
    delete anything unless the repo it is standing in then is provably the
    same one. Any failure yields no baseline file, so the comparison has
    nothing to read and reports hygiene "unknown" — never "clean" (zero !=
    unknown) — and, with no verified baseline, sweeps nothing.

    Limitation: `--untracked-files=all` still does not list ignored paths, so
    a probe written inside a gitignored directory is invisible to both the
    report and the sweep. Probes belong in non-ignored paths.
    """
    repo_root = _resolve_repo_root()
    if repo_root is None:
        return
    entries = _git_status_lines(repo_root)
    if entries is None:
        return
    payload = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": repo_root,
        "entries": entries,
    }
    try:
        critic_adjustments.atomic_write_json(
            os.path.join(output_dir, ".worktree-baseline.json"), payload
        )
    except OSError:
        pass


def _check_worktree_hygiene(output_dir):
    """Compare current git status to the step-3 baseline; sweep probe residue.

    Writes and returns worktree-hygiene.json.

    The sweep is gated on a verified baseline. The snapshot records the repo
    root it measured; this function resolves the root it is standing in now
    and deletes nothing unless both exist and name the same repo. No baseline
    means no delete, ever — removing files from a repo this run never
    measured would be acting on a filename alone, and a run directory reused
    across repos, or a process whose CWD moved, is exactly how that happens.
    An unverified pair reports "unknown", never "clean".

    Within a verified repo, only UNTRACKED files whose BASENAME carries
    PROBE_MARKER are deleted: the reserved name guarantees nothing
    user-owned can match it, matching on the basename keeps a marker-named
    *directory* from condemning the ordinary files inside it, and probes are
    new files by construction, so a tracked path carrying the marker is
    somebody's versioned work rather than residue. Everything else is
    reported untouched because it may be the user's — "changed during
    review" is informational, not blame. A targeted unlink is the only
    mutation this function makes; it never resets, cleans, stashes, or
    unstages, because the reviewed repo is the requester's live tree and may
    hold uncommitted work. A probe someone staged is therefore reported, not
    deleted.
    """
    result = {
        "schema": 1,
        "status": "unknown",
        "new_files": [],
        "changed_files": [],
        "probe_residue_removed": [],
    }
    baseline = None
    baseline_root = None
    baseline_path = os.path.join(output_dir, ".worktree-baseline.json")
    if os.path.isfile(baseline_path):
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") if isinstance(data, dict) else None
            root = data.get("repo_root") if isinstance(data, dict) else None
            if isinstance(entries, list) and isinstance(root, str) and root:
                baseline = {e for e in entries if isinstance(e, str)}
                baseline_root = root
        except (OSError, json.JSONDecodeError):
            baseline = None
            baseline_root = None

    # The identity gate. Everything below — the sweep included — happens
    # only inside the repo the baseline actually measured.
    current_root = _resolve_repo_root()
    verified_root = (
        current_root
        if current_root is not None and current_root == baseline_root
        else None
    )

    if verified_root is not None:
        current = _git_status_lines(verified_root)
        if current is not None:
            # Sweep probe residue first — a probe left behind by a dead agent
            # is pipeline-owned by construction of the reserved name, so
            # removing it also keeps it out of the comparison below, where it
            # would otherwise read as a foreign change. Porcelain format: two
            # status chars, a space, then the path, reported relative to the
            # repo root; `--untracked-files=all` lists every untracked file
            # individually, so a probe inside a directory that did not exist
            # at baseline is visible here instead of being hidden behind a
            # single "?? newdir/" entry. The path is used exactly as git
            # printed it: git quotes any path that could carry leading or
            # trailing whitespace, so stripping cannot rescue a path but can
            # remap one — it would resolve "probe.go " to a different file.
            remaining = []
            for line in current:
                path = line[3:]
                abs_path = os.path.join(verified_root, path)
                if (
                    line[:2] == "??"
                    and PROBE_MARKER in os.path.basename(path)
                    # Defense in depth, and the one thing that stops a
                    # marker-named symlink to a directory from being
                    # unlinked: only regular files (and links to them) are
                    # residue.
                    and os.path.isfile(abs_path)
                ):
                    try:
                        os.remove(abs_path)
                    except OSError:
                        # Residue we could not remove is still residue:
                        # report it as an ordinary entry rather than
                        # claiming a sweep.
                        remaining.append(line)
                    else:
                        result["probe_residue_removed"].append(path)
                    continue
                remaining.append(line)
            current = remaining

            appeared = sorted(set(current) - baseline)
            result["new_files"] = [e for e in appeared if e.startswith("??")]
            result["changed_files"] = [
                e for e in appeared if not e.startswith("??")
            ]
            result["status"] = (
                "changed_during_review" if appeared else "clean"
            )
    # An unverified pair, or a status run that failed, leaves the status at
    # its "unknown" default: with nothing to compare against, reporting
    # "clean" would publish an absent measurement as a measured zero.

    try:
        critic_adjustments.atomic_write_json(
            os.path.join(output_dir, "worktree-hygiene.json"), result
        )
    except OSError:
        # The in-process result still reaches step 11's pipeline result, so
        # an unwritable artifact costs the record, not the measurement.
        pass
    return result


def _orchestrate_step_3(mode, config, state, context, output_dir):
    context_path = os.path.join(output_dir, "review-context.json")

    # Run context.py to collect git context, PR metadata, etc.
    gather_cmd = [sys.executable, str(SCRIPTS_DIR / "context.py"),
                  "--output-dir", output_dir]
    if mode == "pr":
        pr_number = config.get("pr_number", "")
        if pr_number:
            gather_cmd.extend(["--pr-number", pr_number])
    else:
        gather_cmd.append("--branch")
        if mode == "incremental":
            gather_cmd.append("--incremental")
    git_range = config.get("git_range")
    if git_range:
        gather_cmd.extend(["--git-range", git_range])

    stdout, ok = _run_subprocess(gather_cmd, timeout=CONTEXT_GATHER_TIMEOUT)
    # Re-read context (context.py writes review-context.json)
    if os.path.isfile(context_path):
        try:
            with open(context_path) as f:
                context = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Hydrate state from gathered context
    if context.get("has_unfetched_issues"):
        state["resolved_params"]["has_unfetched_issues"] = True
    # Store git range in resolved_params for downstream steps
    git = context.get("git", {})
    if git.get("git_range"):
        state["resolved_params"]["git_range"] = git["git_range"]

    # Trusted-branch dependency refresh — deterministic detection only.
    # Execution belongs to the orchestrator via the step 3 briefing; the
    # script never installs anything.
    if config.get("refresh_dependencies"):
        state["dependency_refresh"] = _detect_dependency_refresh_state(context)

    # Baseline for the step-11 hygiene comparison. Taken at the end of
    # context gathering — the earliest point the run has a settled view of
    # the tree — so everything already there is recorded as the user's
    # pre-existing state, and everything that appears afterwards (the
    # orchestrator's own step-3 dependency refresh included) is measured
    # as having changed during the review rather than blamed on anyone.
    _capture_worktree_baseline(output_dir)

    return context


def _orchestrate_step_5(mode, config, state, context, output_dir):
    if config.get("refresh_dependencies"):
        detection = state.get("dependency_refresh") or {}
        skipped_reason = detection.get("skipped_reason")
        if skipped_reason in DEPENDENCY_REFRESH_SKIP_REASONS:
            dirty_files = detection.get("dirty_files")
            verification = {
                "skipped": True,
                "skipped_reason": skipped_reason,
                "dirty_files": [
                    path for path in (
                        dirty_files if isinstance(dirty_files, list) else []
                    )
                    if isinstance(path, str)
                ][:_MAX_DIRTY_FILES],
            }
        else:
            try:
                repo_root = _git_output("rev-parse", "--show-toplevel")
                verification = verify_dependency_refresh(repo_root, output_dir)
            except Exception:
                verification = {
                    "report_present": False,
                    "commands_allowed": None,
                    "disallowed_commands": [],
                    "tracked_files_dirty": None,
                    "dirty_files": [],
                    "verification_failed": True,
                }
        with open(
            os.path.join(output_dir, "dependency-refresh-verification.json"),
            "w",
        ) as verification_file:
            json.dump(verification, verification_file, indent=2, sort_keys=True)
        state["dependency_refresh_verification"] = verification

    # Run plan_dispatch.py to determine which agents to dispatch
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    if git_range:
        planner_cmd = [
            sys.executable, str(SCRIPTS_DIR / "plan_dispatch.py"),
            "--mode", mode,
            "--git-range", git_range,
            "--output-dir", output_dir,
            "--host", _host(config),
        ]
        changed_csv = git.get("changed_files_csv", "")
        if changed_csv:
            planner_cmd.extend(["--changed-files-list", changed_csv])
        # Pass review context for PR metadata triage (title, body, labels, branch, issues)
        ctx_path = os.path.join(output_dir, "review-context.json")
        if os.path.isfile(ctx_path):
            planner_cmd.extend(["--review-context", ctx_path])
        if config.get("quick"):
            planner_cmd.append("--quick")

        stdout, ok = _run_subprocess(planner_cmd, timeout=60)

        plan_path = os.path.join(output_dir, "dispatch-plan.json")
        if os.path.isfile(plan_path):
            try:
                plan = _load_dispatch_plan(plan_path)
                if ok:
                    _preserve_initial_dispatch_plan(output_dir, plan)
                agents = plan["agents"]
                state["dispatch_plan_summary"] = {
                    "dispatched": sum(1 for a in agents if a.get("status") in DISPATCHED_STATUSES),
                    "skipped": sum(1 for a in agents if a.get("status") in SKIPPED_STATUSES),
                    "conditional": sum(1 for a in agents if a.get("status") in DISPATCHED_STATUSES and "conditional" in a.get("reason", "").lower()),
                }
                # Store agent details for human-readable step 5 summary
                state["dispatch_plan_agents"] = [
                    {
                        "name": a["name"],
                        "focus": a.get("focus", ""),
                        "status": a.get("status", ""),
                        "reason": a.get("reason", ""),
                    }
                    for a in agents
                ]
                # Surface coverage warnings (e.g. unrecognized source language).
                state["dispatch_plan_warnings"] = plan.get("warnings", [])
            except (json.JSONDecodeError, OSError):
                state["dispatch_plan_summary"] = {}
                state["dispatch_plan_agents"] = []
                state["dispatch_plan_warnings"] = []
    else:
        state["dispatch_plan_summary"] = {}
        state["dispatch_plan_agents"] = []
        state["dispatch_plan_warnings"] = []

    return context


def _orchestrate_step_6(mode, config, state, context, output_dir):
    plan_path = os.path.join(output_dir, "dispatch-plan.json")
    if os.path.isfile(plan_path):
        try:
            plan = _load_dispatch_plan(plan_path)
            dispatched = [
                {
                    "name": a["name"],
                    "domain": a.get("domain", ""),
                    # Adapter fields (present only for repo-contributed
                    # reviewers). Carried so step 6 can emit the ref-mode
                    # bootstrap command instead of a plain --agent call.
                    "adapter": a.get("adapter"),
                    "ref": a.get("ref"),
                    "label": a.get("label"),
                    "channel": a.get("channel"),
                    "execution": a.get("execution"),
                    "model": a.get("model"),
                    "scope_domains": a.get("scope_domains"),
                }
                for a in plan.get("agents", [])
                if a.get("status") in DISPATCHED_STATUSES
            ]
            state["dispatched_agents"] = dispatched
            # Recompute dispatch_plan_summary from final plan (post-override)
            all_agents = plan["agents"]
            state["dispatch_plan_summary"] = {
                "dispatched": sum(
                    1 for a in all_agents
                    if a.get("status") in DISPATCHED_STATUSES
                ),
                "skipped": sum(
                    1 for a in all_agents
                    if a.get("status") in SKIPPED_STATUSES
                ),
                "conditional": sum(
                    1 for a in all_agents
                    if a.get("status") in DISPATCHED_STATUSES
                    and "conditional" in a.get("reason", "").lower()
                ),
            }
        except (json.JSONDecodeError, OSError):
            state["dispatched_agents"] = []
    else:
        state["dispatched_agents"] = []

    return context


def _orchestrate_step_7(mode, config, state, context, output_dir):
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    base_ref = git.get("base_ref", "main")

    head_sha, _ = _run_subprocess(["git", "rev-parse", "HEAD"])
    if not head_sha or len(head_sha) < 7:
        head_sha = "0000000"

    baseline_path = os.path.join(output_dir, ".branch-review-baseline.json")
    review_count = 0
    if os.path.isfile(baseline_path):
        try:
            with open(baseline_path) as f:
                old = json.load(f)
            review_count = old.get("review_count", 0)
        except (json.JSONDecodeError, OSError):
            pass

    baseline = {
        "last_reviewed_sha": head_sha,
        "last_reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_type": mode,
        "review_count": review_count + 1,
        "base_ref": base_ref,
        "git_range_used": git_range or f"{head_sha}..HEAD",
    }
    with open(baseline_path, "w") as f:
        json.dump(baseline, f, indent=2)

    return context


def _orchestrate_step_8(mode, config, state, context, output_dir):
    # Hard readiness gate: check if all dispatched agents have finished
    # before allowing reconciliation to proceed.
    # Exit code 0 = all done, 2 = agents still running, 1 = error.
    status_cmd = [
        sys.executable, str(SCRIPTS_DIR / "agents_status.py"),
        "--output-dir", output_dir,
    ]
    try:
        r = subprocess.run(status_cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 2:
            previous_waiting = state.get("waiting_on_agents", {})
            # Agents still running — parse text output for names
            running = []
            not_dispatched = []
            for line in r.stdout.splitlines():
                stripped = line.strip()
                if "RUNNING" in stripped and "NOT_DISPATCHED" not in stripped:
                    # Lines look like: "agent-name           RUNNING   (3m 42s)"
                    name = stripped.split()[0]
                    running.append(name)
                elif "NOT_DISPATCHED" in stripped:
                    name = stripped.split()[0]
                    not_dispatched.append(name)
            state["waiting_on_agents"] = {
                "running": running,
                "not_dispatched": not_dispatched,
                "status_output": r.stdout.strip(),
            }
            if previous_waiting.get("first_waiting_at"):
                state["waiting_on_agents"]["first_waiting_at"] = previous_waiting["first_waiting_at"]
            else:
                state["waiting_on_agents"]["first_waiting_at"] = datetime.now(timezone.utc).isoformat()
            # Read per-agent timeout for escalation threshold
            agent_timeout = DEFAULT_AGENT_TIMEOUT
            ctx_path = os.path.join(output_dir, "review-context.json")
            if os.path.isfile(ctx_path):
                try:
                    with open(ctx_path) as f:
                        ctx_data = json.load(f)
                    agent_timeout = ctx_data.get("review", {}).get(
                        "agent_timeout_seconds", DEFAULT_AGENT_TIMEOUT
                    )
                except (json.JSONDecodeError, OSError):
                    pass
            state["waiting_on_agents"]["agent_timeout_seconds"] = agent_timeout
            try:
                first_waiting = datetime.fromisoformat(
                    state["waiting_on_agents"]["first_waiting_at"]
                )
                elapsed = (datetime.now(timezone.utc) - first_waiting).total_seconds()
            except (ValueError, KeyError):
                elapsed = 0
            if elapsed < agent_timeout + AGENT_WAIT_GRACE_SECONDS:
                return context
        else:
            state.pop("waiting_on_agents", None)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Gate is best-effort; if checker fails, proceed normally and
        # avoid carrying stale waiting state forward.
        state.pop("waiting_on_agents", None)

    # Materialize human-facing Markdown from every settled canonical JSON
    # before reconciliation begins. This also runs when the best-effort status
    # checker fails: its failure does not make published JSON unsafe to render.
    reviewer_markdown = {
        "ran": True,
        "written": 0,
        "expected": 0,
        "status": "failed",
    }
    materialization_failed = False
    written_markdown = set()
    try:
        written_paths = _materialize_reviewer_markdown(
            output_dir, str(SCRIPTS_DIR / "agent" / "output.py"),
        )
        written_markdown = {
            os.path.abspath(os.fspath(path)) for path in written_paths
        }
    except Exception as err:  # noqa: BLE001 — best-effort by design
        materialization_failed = True
        print(
            f"reviewer markdown materialization failed: {err}",
            file=sys.stderr,
        )

    expected_markdown = set()
    try:
        expected_markdown = {
            os.path.abspath(os.path.join(
                output_dir,
                name[: -len(".json")] + ".md",
            ))
            for name in os.listdir(output_dir)
            if name.endswith("-review.json")
        }
    except Exception as err:  # noqa: BLE001 — best-effort by design
        materialization_failed = True
        print(
            f"reviewer markdown materialization failed: {err}",
            file=sys.stderr,
        )

    reviewer_markdown["written"] = len(written_markdown)
    reviewer_markdown["expected"] = len(expected_markdown)
    if not materialization_failed:
        reviewer_markdown["status"] = (
            "complete"
            if written_markdown == expected_markdown
            else "partial"
        )
    state["reviewer_markdown"] = reviewer_markdown
    degradation = state.setdefault("degradation", {})
    if reviewer_markdown["status"] == "complete":
        degradation.pop("reviewer_markdown_incomplete", None)
        if not degradation:
            state.pop("degradation", None)
    else:
        degradation["reviewer_markdown_incomplete"] = True

    cp_path = os.path.join(output_dir, "change-purpose.md")
    if os.path.isfile(cp_path):
        try:
            with open(cp_path) as f:
                state["change_purpose"] = f.read().strip()
        except OSError:
            pass

    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    if git_range and not state.get("change_purpose"):
        log_out, _ = _run_subprocess(["git", "log", "--format=%s", git_range])
        if log_out:
            state["commit_messages"] = log_out.strip().split("\n")

    plan_path = os.path.join(output_dir, "dispatch-plan.json")
    if os.path.isfile(plan_path):
        try:
            plan = _load_dispatch_plan(plan_path)
            dispatched_names = [
                a["name"] for a in plan["agents"]
                if a.get("status") in DISPATCHED_STATUSES
            ]
            review_files = []
            completed = []
            for name in dispatched_names:
                # Only a trailing "-reviewer" maps to "-review" — repo
                # reviewer ids may carry "reviewer" mid-string.
                stem = (
                    f"{name[: -len('-reviewer')]}-review"
                    if name.endswith("-reviewer") else name
                )
                review_file = os.path.join(output_dir, f"{stem}.json")
                if os.path.isfile(review_file):
                    completed.append(name)
                    review_files.append(review_file)
            state["agents"] = {
                "dispatched": dispatched_names,
                "completed": completed,
                "failed": [],
                "review_files": review_files,
            }
        except (json.JSONDecodeError, OSError):
            pass

    # Build reconciliation context (pre-gather all data for the reconciliator)
    recon_ctx_cmd = [
        sys.executable, str(SCRIPTS_DIR / "reconciliation_context.py"),
        "--output-dir", output_dir,
        "--git-range", git_range,
        "--changed-files", context.get("git", {}).get("changed_files_csv", ""),
    ]
    cp = state.get("change_purpose", "")
    if cp:
        recon_ctx_cmd.extend(["--change-purpose", cp])
    pr_id = config.get("pr_number", "")
    if pr_id:
        recon_ctx_cmd.extend(["--pr-id", str(pr_id)])
    # Pass dispatched agents when real dispatch metadata exists.
    # Distinguish three cases:
    # 1. dispatched is non-empty → pass agent names (filter to those agents)
    # 2. dispatched is empty BUT dispatch-plan.json exists → plan ran and
    #    selected 0 agents (e.g., docs-only change). Pass empty string so
    #    reconciliation_context.py loads nothing (not stale files).
    # 3. No dispatch plan file → truly unknown, omit flag so
    #    reconciliation_context.py falls back to scanning all *-review.json.
    agents_info = state.get("agents")
    if agents_info is not None:
        dispatched = agents_info.get("dispatched", [])
        if dispatched:
            recon_ctx_cmd.extend(["--dispatched-agents", ",".join(dispatched)])
        elif os.path.isfile(plan_path):
            recon_ctx_cmd.extend(["--dispatched-agents", ""])
    _, ctx_ok = _run_subprocess(recon_ctx_cmd, timeout=30)
    recon_ctx_path = os.path.join(output_dir, "reconciliation-context.md")
    if not ctx_ok or not os.path.isfile(recon_ctx_path):
        raise RuntimeError(
            "reconciliation_context.py failed — cannot proceed to "
            "reconciliation without a valid context file. "
            f"Check stderr above. Expected: {recon_ctx_path}"
        )

    return context


def _orchestrate_step_9(mode, config, state, context, output_dir):
    # Load inline coverage gaps and deferred-review claims computed at
    # reconciliation so the report briefing preserves the distinction
    # between proof gaps and unverified claims.
    recon_json_path = os.path.join(output_dir, "reconciliation-context.json")
    gaps = {}
    claims = {}
    if os.path.isfile(recon_json_path):
        try:
            with open(recon_json_path) as f:
                recon = json.load(f)
            coverage = (
                recon.get("inline_coverage") or {}
                if isinstance(recon, dict)
                else {}
            )
            if isinstance(coverage, dict):
                raw_gaps = coverage.get("files_never_inline") or {}
                if isinstance(raw_gaps, dict):
                    gaps = raw_gaps
                raw_claims = coverage.get("files_deferred_reviewed") or {}
                if isinstance(raw_claims, dict):
                    claims = raw_claims
        except (json.JSONDecodeError, OSError):
            gaps = {}
            claims = {}
    state["inline_coverage_gaps"] = gaps
    state["inline_coverage_claims"] = claims

    return context


def _orchestrate_step_10(mode, config, state, context, output_dir):
    # Read reconciliation verdict for quick-mode critic skip decision
    findings_path = os.path.join(output_dir, "review-findings.json")
    if os.path.isfile(findings_path):
        try:
            with open(findings_path) as f:
                findings = json.load(f)
            state["reconciliation_verdict"] = findings.get("verdict", "")
        except (json.JSONDecodeError, OSError):
            state["reconciliation_verdict"] = ""

    # Record critic skip decision for telemetry.
    # Clear any stale decision first (step 10 may be rerun after
    # review-findings.json changes from approve/comment to a higher verdict).
    state.setdefault("step_decisions", {}).pop("10", None)
    is_quick = config.get("quick", False)
    recon_verdict = state.get("reconciliation_verdict", "")
    if is_quick and recon_verdict.lower() in ("approve", "comment"):
        state["step_decisions"]["10"] = {
            "critic_skipped": True,
            "reason": f"quick mode + reconciliation verdict: {recon_verdict}",
        }

    return context


def _orchestrate_step_11(mode, config, state, context, output_dir):
    # Read critic verdict from file (written by LLM at step 10)
    critic_path = os.path.join(output_dir, "decision-critic-verdict.json")
    if os.path.isfile(critic_path):
        try:
            with open(critic_path) as f:
                critic_data = json.load(f)
            raw_verdict = critic_data.get("verdict", "unavailable")
            # Map SKIPPED → unavailable so downstream consumers
            # (pirategoat-bot) correctly show "not cross-validated"
            state["critic_verdict"] = (
                "unavailable" if raw_verdict == "SKIPPED" else raw_verdict
            )
        except (json.JSONDecodeError, OSError):
            state["critic_verdict"] = "unavailable"
    else:
        state["critic_verdict"] = "unavailable"

    verdict_path = os.path.join(output_dir, "review-verdict.json")
    verdict_data = None
    if os.path.isfile(verdict_path):
        try:
            with open(verdict_path) as f:
                verdict_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    report_path = os.path.join(output_dir, "review-report.md")
    findings_path = os.path.join(output_dir, "review-findings.json")
    degradation_notes = []

    # Carry any pending critic adjustments into the findings ledger before
    # the verdict sync — but only under REVISE, the one verdict that
    # sanctions them. The step-10 REVISE briefing has the orchestrator
    # spot-check each entry and mark the refuted ones `rejected` before
    # running this same apply, so here it is the defensive re-run: bot
    # mode follows no briefing at all, and an interactive run can still
    # stop short of step 10's instructions. Idempotence makes the re-run
    # free for a run that already applied.
    # The verdict gate is what keeps that re-run from becoming a bypass:
    # adjustments are a REVISE-only channel, so a critic that writes them
    # alongside STAND, ESCALATE, or a skipped verdict would otherwise get
    # them applied with no orchestrator spot-check at all. Under any other
    # verdict a still-pending file is surfaced as a degradation and never
    # applied. Pending is counted, not assumed: a file whose entries have
    # all landed is the ordinary post-apply state of a REVISE run whose
    # step 11 is re-entered, and says nothing.
    # Ordering note: nothing re-runs the reconciliator after this point —
    # compute_next_step only routes forward (candidates are `s >
    # current_step`), so a completed step 8 is never re-entered — and the
    # applied_critic_adjustments record in the findings file survives to
    # the final artifact.
    if os.path.isfile(findings_path):
        critic_verdict = state.get("critic_verdict", "unavailable")
        if critic_verdict == "REVISE":
            try:
                critic_adjustments.apply_adjustments(output_dir)
            except (ValueError, OSError, json.JSONDecodeError) as err:
                degradation_notes.append(
                    f"critic adjustments not applied: {err}"
                )
        else:
            try:
                pending = critic_adjustments.pending_count(output_dir)
            except (ValueError, OSError, json.JSONDecodeError) as err:
                degradation_notes.append(
                    f"critic adjustments not readable: {err}"
                )
            else:
                if pending:
                    degradation_notes.append(
                        f"critic adjustments present but critic verdict is "
                        f"{critic_verdict} — not applied (adjustments are a "
                        f"REVISE-only channel)"
                    )

    # Hygiene: the reviewed repo is the requester's live working tree, so
    # finalize accounts for what the run left in it. The sweep runs here
    # rather than nowhere because a probe that outlived the command that
    # created it is pipeline-owned litter; everything else is reported and
    # left alone, since it may be the requester's own work.
    hygiene = _check_worktree_hygiene(output_dir)
    # Measured hygiene rides the pipeline result as data; an unmeasured run
    # carries null rather than a zeroed summary, so a consumer can never
    # read "nothing was measured" as "nothing happened".
    hygiene_summary = None
    if hygiene.get("status") != "unknown":
        hygiene_summary = {
            "status": hygiene.get("status"),
            "new_files": len(hygiene.get("new_files", [])),
            "changed_files": len(hygiene.get("changed_files", [])),
            "probe_residue_removed": len(
                hygiene.get("probe_residue_removed", [])
            ),
        }
    # Only the sweep degrades the run. A requester editing their own tree
    # during a review is routine, and `status` is a bot contract that has to
    # keep meaning "the review pipeline underperformed" — spending it on
    # someone else's ordinary keystrokes would teach every consumer to
    # ignore it. Swept residue is different in kind: it is a pipeline
    # participant breaking the rule that a probe is created, run, and
    # deleted by the same command.
    if hygiene.get("probe_residue_removed"):
        degradation_notes.append(
            f"probe residue swept at finalize: "
            f"{len(hygiene['probe_residue_removed'])} file(s) — a probe "
            "should be deleted in the same command that created it"
        )
    # "unknown" is silent by construction now: with no verified baseline
    # nothing was swept and nothing was compared, so there is no outcome to
    # report — only the absence of one, which `worktree_hygiene: null` and
    # the hygiene artifact already state.

    if not verdict_data:
        degradation_notes.append("review-verdict.json not found")
    if not os.path.isfile(report_path):
        degradation_notes.append("review-report.md not found")
        alt = os.path.join(output_dir, "review-findings.md")
        report_path = alt if os.path.isfile(alt) else None
    if not os.path.isfile(findings_path):
        degradation_notes.append("review-findings.json not found")

    verdict = verdict_data.get("verdict", "COMMENT") if verdict_data else "COMMENT"

    # Rule 23: update review-findings.json verdict to match. This is the
    # other writer of the ledger critic_adjustments.py keeps crash-safe, so
    # it shares that module's atomic write: a truncating open here would
    # leave the artifact destroyable by a crash mid-write no matter how
    # carefully the adjustments path replaces it, and this write is the
    # last one the run performs.
    if verdict_data and os.path.isfile(findings_path):
        try:
            with open(findings_path) as f:
                findings = json.load(f)
            # A non-object ledger has nowhere to carry a verdict. The
            # subscript assignment would raise TypeError past this except
            # tuple and crash finalize outright — the same shape hole the
            # adjustments apply closed on its own side.
            if not isinstance(findings, dict):
                degradation_notes.append(
                    "verdict sync skipped: review-findings.json is not an "
                    "object"
                )
            else:
                findings["verdict"] = verdict
                critic_adjustments.atomic_write_json(findings_path, findings)
        except (json.JSONDecodeError, OSError):
            pass

    # Computed after the verdict sync: a degradation the sync discovers has
    # to reach the status it is reported beside, or the run publishes
    # "success" while carrying a note that says otherwise.
    status = "success" if not degradation_notes else "degraded"

    pipeline_result = {
        "status": status,
        "verdict": verdict,
        "report_path": report_path if report_path and os.path.isfile(report_path) else None,
        "findings_path": findings_path if os.path.isfile(findings_path) else None,
        "critic_verdict": state.get("critic_verdict", "unavailable"),
        "degradation_notes": degradation_notes,
        "review_baseline_saved": os.path.isfile(
            os.path.join(output_dir, ".branch-review-baseline.json")
        ),
        "worktree_hygiene": hygiene_summary,
    }
    result_path = os.path.join(output_dir, "pipeline-result.json")
    with open(result_path, "w") as f:
        json.dump(pipeline_result, f, indent=2)

    state["verdict"] = verdict
    state["review_verdict"] = verdict
    state["pipeline_status"] = status

    return context


def _orchestrate_step(step, mode, config, state, context, output_dir):
    """Run step-specific side effects (subprocesses, file I/O).

    Called by main() BEFORE get_step_guidance(). Mutates state and context
    in place. Returns the (possibly updated) context dict.
    """
    if step == 2:
        return _orchestrate_step_2(
            mode, config, state, context, output_dir
        )
    if step == 3:
        return _orchestrate_step_3(
            mode, config, state, context, output_dir
        )
    if step == 5:
        return _orchestrate_step_5(
            mode, config, state, context, output_dir
        )
    if step == 6:
        return _orchestrate_step_6(
            mode, config, state, context, output_dir
        )
    if step == 7:
        return _orchestrate_step_7(
            mode, config, state, context, output_dir
        )
    if step == 8:
        return _orchestrate_step_8(
            mode, config, state, context, output_dir
        )
    if step == 9:
        return _orchestrate_step_9(
            mode, config, state, context, output_dir
        )
    if step == 10:
        return _orchestrate_step_10(
            mode, config, state, context, output_dir
        )
    if step == 11:
        return _orchestrate_step_11(
            mode, config, state, context, output_dir
        )
    return context

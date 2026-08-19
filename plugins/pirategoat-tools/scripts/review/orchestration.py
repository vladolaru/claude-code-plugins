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

    if not verdict_data:
        degradation_notes.append("review-verdict.json not found")
    if not os.path.isfile(report_path):
        degradation_notes.append("review-report.md not found")
        alt = os.path.join(output_dir, "review-findings.md")
        report_path = alt if os.path.isfile(alt) else None
    if not os.path.isfile(findings_path):
        degradation_notes.append("review-findings.json not found")

    verdict = verdict_data.get("verdict", "COMMENT") if verdict_data else "COMMENT"
    status = "success" if not degradation_notes else "degraded"

    # Rule 23: update review-findings.json verdict to match
    if verdict_data and os.path.isfile(findings_path):
        try:
            with open(findings_path) as f:
                findings = json.load(f)
            findings["verdict"] = verdict
            with open(findings_path, "w") as f:
                json.dump(findings, f, indent=2)
        except (json.JSONDecodeError, OSError):
            pass

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

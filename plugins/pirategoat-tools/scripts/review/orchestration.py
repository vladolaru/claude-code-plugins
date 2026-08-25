"""Side-effecting step orchestration for the review pipeline."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        REVIEW_RECORD_MD,
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
        load_dependency_refresh_report,
        observe_tracked_worktree,
    )
    from . import atomic_io
    from .atomic_io import atomic_write_json, atomic_write_text
    from .reviewer_lifecycle import close_review_intake, review_paths
    from .reviewer_names import derive_reviewer_name
    from .briefings import _render_review_accounting_section
    from .reconciliation_context import (
        review_accounting_from_context,
        strip_severity_floor_markers,
    )
    from . import critic_adjustments
    from . import manifest_sections
    from . import synthesis_lifecycle
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.pipeline_contract import (
        AGENT_WAIT_GRACE_SECONDS,
        CONTEXT_GATHER_TIMEOUT,
        DEFAULT_AGENT_TIMEOUT,
        REVIEW_RECORD_MD,
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
        load_dependency_refresh_report,
        observe_tracked_worktree,
    )
    from review import atomic_io
    from review.atomic_io import atomic_write_json, atomic_write_text
    from review.reviewer_lifecycle import close_review_intake, review_paths
    from review.reviewer_names import derive_reviewer_name
    from review.briefings import _render_review_accounting_section
    from review.reconciliation_context import (
        review_accounting_from_context,
        strip_severity_floor_markers,
    )
    from review import critic_adjustments
    from review import manifest_sections
    from review import synthesis_lifecycle

from git_paths import decode_git_c_quoted_path


# Reserved marker for pipeline-created probe files. Nothing user-owned
# may carry it, which is what makes the step-11 residue sweep a safe,
# targeted delete instead of a tree-wide git reset/clean (forbidden:
# the reviewed repo is the user's live tree and may hold uncommitted work).
PROBE_MARKER = "pirategoat-probe"


def _is_normalized_relative_path(path):
    if not isinstance(path, str) or not path or os.path.isabs(path):
        return False
    return all(
        component not in ("", ".", "..") for component in path.split("/")
    )


# One spelling of the status arguments for both the step-3 snapshot and the
# step-11 comparison, so the two can never drift into reporting a format
# difference as a worktree change. `--untracked-files=all` is load-bearing:
# plain porcelain collapses an untracked directory into a single "?? dir/"
# entry without recursing, which would both coarsen the comparison and hide
# a probe file created inside a new directory from the sweep. Porcelain
# reports every path relative to the repository root regardless of where it
# is invoked, which is why both halves pin the root explicitly below.
_GIT_STATUS_ARGS = ["status", "--porcelain", "--untracked-files=all"]

# The usage capture correlates the main session transcript with every
# subagent transcript the run produced, so its cost scales with the JSONL
# the run wrote — tens of megabytes for a large review. A minute is
# generous for that parse and small enough that a hung capture cannot hold
# finalize open; the timeout expiring simply leaves the run unmeasured.
USAGE_SNAPSHOT_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Dispatch Plan Persistence
# ---------------------------------------------------------------------------

def _preserve_initial_dispatch_plan(output_dir, plan):
    """Atomically preserve the planner baseline without blocking the review.

    Any prior baseline is removed first so a failed measurement write cannot
    make an older plan look like the current run's deterministic output.
    """
    initial_path = os.path.join(output_dir, "dispatch-plan.initial.json")
    try:
        try:
            os.remove(initial_path)
        except FileNotFoundError:
            pass

        atomic_write_json(initial_path, plan)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(initial_path)
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


def _load_output_module(output_builder_path: str):
    """Load agent/output.py by exact adjacent path.

    The same contract the telemetry and dispatch-status loaders use, so a
    long-lived process can never render with a foreign checkout's
    semantics. Both users of output.py's renderers go through this one
    loader: `_materialize_markdown` (the derived-Markdown families) and
    `assemble_review_record` (the record's shared body).
    """
    spec = importlib.util.spec_from_file_location(
        "_pirategoat_review_output", output_builder_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materialize_markdown(
    output_dir: str, output_builder_path: str, suffix: str = "-review.json",
) -> list:
    """Render derived Markdown from the settled JSONs in `output_dir`.

    One entry point for both derived families: the per-reviewer
    `<reviewer>-review.md` the step-8 readiness gate writes (the default
    suffix) and `review-findings.md`, which steps 9 and 11 render from the
    reconciliation ledger. The renderer itself lives in output.py; this is
    only the caller, and there is deliberately no second one.
    """
    module = _load_output_module(output_builder_path)
    return module.materialize_markdown(output_dir, suffix=suffix)


def _load_final_review(
    output_builder_path: str, review_path: str, reviewer: str
) -> dict:
    """Load one semantic completion through the final-review authority."""
    module = _load_output_module(output_builder_path)
    return module.load_review_document(review_path, reviewer)


_FINDINGS_JSON = "review-findings.json"
_FINDINGS_MD = "review-findings.md"


# The two verdict layers, and the one place they meet. Reviewers, the
# reconciliator, and critic batches all speak the per-review vocabulary of
# `schemas/review-output.ts` (`verdict_rules.verdict_for_counts`); the outer
# pipeline `pipeline-result.json` publishes — and pirategoat-bot maps onto
# GitHub actions — speaks APPROVE/COMMENT/REQUEST_CHANGES.
#
# All FIVE ledger verdicts are here on purpose. `block` is real (any
# critical finding, or three highs) and maps to REQUEST_CHANGES; omitting it
# would publish COMMENT for a critical-finding review — the exact failure
# deriving the verdict from the ledger exists to kill. `not_applicable` is
# defensive: it belongs to a single reviewer with nothing in scope, and a
# reconciled ledger should never carry it.
_LEDGER_TO_REVIEW_VERDICT = {
    "block": "REQUEST_CHANGES",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
    "approve": "APPROVE",
    "not_applicable": "COMMENT",
}

# What the step-10 briefing may point the decision critic at, best first.
# Existence decides, not a flag: the branch this replaced read a
# `report_synthesis_failed` key no writer under scripts/ ever set.
#
# `review-report.md` is deliberately NOT a candidate: it does not exist
# yet at step 10 — it is authored once at step 11, after this critic has
# run — and listing a file that cannot be there would make the fallback
# branch fire on every single run. The record is what the critic reads.
_CRITIC_SOURCE_CANDIDATES = (
    REVIEW_RECORD_MD, _FINDINGS_MD, _FINDINGS_JSON,
)

# The complete output of one critic attempt. Step 10 removes this set before
# handing off a replacement, so every reader sees either the new attempt's
# snapshot or an honestly incomplete attempt — never the prior decision.
_CRITIC_OUTPUT_FILENAMES = (
    "decision-critic-findings.md",
    critic_adjustments.ADJUSTMENTS_FILENAME,
    critic_adjustments.CRITIC_VERDICT_FILENAME,
)


def _record_findings_markdown(state, outcome):
    """Record the findings-render outcome AND its degradation flag together.

    Both render seams (step 9, step 11) call this. When they each did their
    own recording, step 11 updated the outcome but not the flag, so a
    step-9 failure that step 11 repaired left `findings_markdown_incomplete`
    standing — and that flag is a fact the step-10 briefing reads. Two
    writers of one paired fact is how the pair comes apart.
    """
    state["findings_markdown"] = outcome
    degradation = state.setdefault("degradation", {})
    if outcome["status"] == "complete":
        degradation.pop("findings_markdown_incomplete", None)
        if not degradation:
            state.pop("degradation", None)
    else:
        degradation["findings_markdown_incomplete"] = True


def _render_findings_markdown(output_dir: str) -> tuple:
    """Render `review-findings.md` from the reconciliation ledger.

    Returns ``(outcome, error)``: ``outcome`` mirrors the reviewer-Markdown
    vocabulary (``complete`` / ``failed``) with the same written/expected
    accounting, and ``error`` is the exception text when the render failed.
    Never raises — a derived artifact that could not be rendered is a
    degradation to report, never a reason to abort a step.

    ``expected`` is 0 when there is no ledger at all: nothing was asked of
    the renderer, so nothing is missing. That is a different fact from a
    ledger the renderer could not read, which reports ``failed``.
    """
    expected = 1 if os.path.isfile(
        os.path.join(output_dir, _FINDINGS_JSON)
    ) else 0
    outcome = {
        "ran": True, "written": 0, "expected": expected, "status": "complete",
    }
    if not expected:
        return outcome, None
    try:
        written = _materialize_markdown(
            output_dir,
            str(SCRIPTS_DIR / "agent" / "output.py"),
            suffix=_FINDINGS_JSON,
        )
    except Exception as err:  # noqa: BLE001 — best-effort by design
        outcome["status"] = "failed"
        return outcome, str(err)
    outcome["written"] = len(written)
    if not written:
        # The materializer skips a ledger it cannot read (malformed JSON,
        # missing required keys) and says so on stderr rather than
        # raising. An unwritten .md is still an unrendered artifact.
        outcome["status"] = "failed"
        return outcome, f"renderer skipped {_FINDINGS_JSON}"
    return outcome, None


# ---------------------------------------------------------------------------
# Review Record Assembly
# ---------------------------------------------------------------------------

def _sanitized_ledger(findings: dict) -> dict:
    """A copy of the ledger with prose severity-floor markers removed.

    Rendered clean AT THE SOURCE, which is where this strip belongs now:
    it used to live in `build_critic_context`, a Markdown context builder
    that existed only to merge the report and the ledger for the decision
    critic. The record replaced that builder, so the protection moved with
    the reader — a `Severity-floor:` restatement left in prose reads to the
    critic as an instruction not to demote, and demoting is the judgment
    the critic exists to make on its own.

    Copies rather than mutates: `review-findings.json` on disk keeps the
    reviewer's own words, and the structured `severity_floor` field (which
    `render_review_body` renders as its own line) is untouched.
    """
    clean = dict(findings)

    if clean.get("assessment"):
        clean["assessment"] = strip_severity_floor_markers(
            clean["assessment"]
        )
    invalidated = clean.get("invalidated_assessments")
    if isinstance(invalidated, list):
        clean["invalidated_assessments"] = [
            {
                **entry,
                "text": strip_severity_floor_markers(entry["text"]),
            } if isinstance(entry, dict) and entry.get("text") else entry
            for entry in invalidated
        ]

    def _clean_finding(finding):
        if not isinstance(finding, dict):
            return finding
        patched = dict(finding)
        for field in ("title", "description", "recommendation"):
            if patched.get(field):
                patched[field] = strip_severity_floor_markers(patched[field])
        return patched

    for key in ("findings", "findings_removed_by_critic"):
        entries = clean.get(key)
        if isinstance(entries, list):
            clean[key] = [_clean_finding(entry) for entry in entries]

    # The field list has to match what `render_review_body` actually puts
    # in the record, not just the findings. What this replaced stripped the
    # WHOLE report text in one pass, so it could not miss a field; naming
    # fields is what makes an omission possible, and a rendered field the
    # strip skips is a marker reaching the critic through the back door.
    for key in ("checks", "checks_removed_by_critic"):
        checks = clean.get(key)
        if not isinstance(checks, list):
            continue
        clean[key] = [
            {
                field: (
                    strip_severity_floor_markers(value)
                    if field in ("question", "method", "result") and value
                    else value
                )
                for field, value in entry.items()
            } if isinstance(entry, dict) else entry
            for entry in checks
        ]

    positives = clean.get("positive_observations")
    if isinstance(positives, list):
        clean["positive_observations"] = [
            strip_severity_floor_markers(entry) for entry in positives
        ]

    observations = clean.get("observations")
    if isinstance(observations, list):
        clean["observations"] = [
            {
                **entry,
                "note": strip_severity_floor_markers(entry["note"]),
            } if isinstance(entry, dict) and entry.get("note") else entry
            for entry in observations
        ]

    recommendations = clean.get("recommendations")
    if isinstance(recommendations, dict):
        clean["recommendations"] = {
            priority: [
                strip_severity_floor_markers(item) for item in entries
            ] if isinstance(entries, list) else entries
            for priority, entries in recommendations.items()
        }

    return clean


def _render_record_body(findings: dict) -> str:
    """The record's findings/checks body — output.py's own renderer.

    Byte-identical to what `review-findings.md` shows for the same ledger
    (modulo the prose-marker strip above), because it IS the same function.
    A second copy of these sections is how the two documents would
    eventually disagree about a finding.
    """
    module = _load_output_module(str(SCRIPTS_DIR / "agent" / "output.py"))
    return module.render_review_body(_sanitized_ledger(findings))


def _render_run_notes(state: dict) -> str:
    """What the run did to itself, in two lines the ledger cannot carry.

    Both facts already live in pipeline state; nothing is re-derived from
    the filesystem here. An absent fact says so — "not requested" and "not
    recorded" are different from a measured clean result, and none of the
    three may be reported as either of the others.
    """
    lines = ["## Run notes", ""]

    precheck = state.get("dependency_refresh_precheck")
    if not isinstance(precheck, dict):
        lines.append("- Dependency refresh: not requested.")
    elif precheck.get("tracked_files_dirty") is True:
        lines.append(
            "- Dependency refresh: refused before execution because the "
            "tracked worktree was dirty."
        )
    elif precheck.get("tracked_files_dirty") is not False:
        lines.append(
            "- Dependency refresh: refused before execution because the "
            "tracked worktree state was unknown."
        )
    else:
        report = state.get("dependency_refresh_report")
        if not isinstance(report, dict):
            lines.append(
                "- Dependency refresh: requested but not recorded."
            )
        else:
            commands = report.get("commands")
            command_count = len(commands) if isinstance(commands, list) else 0
            lines.append(
                f"- Dependency refresh: {report.get('status')}; "
                f"{command_count} command(s) reported; final tracked files "
                f"dirty: {_tri_state(report.get('tracked_files_dirty'))}."
            )

    summary = state.get("dispatch_plan_summary")
    if isinstance(summary, dict) and summary:
        lines.append(
            f"- Dispatch: {summary.get('dispatched', 0)} dispatched, "
            f"{summary.get('skipped', 0)} skipped "
            f"({summary.get('conditional', 0)} conditional)."
        )
    else:
        lines.append("- Dispatch: no plan summary recorded for this run.")

    agents = state.get("agents")
    discarded_drafts = (
        agents.get("discarded_drafts") if isinstance(agents, dict) else None
    )
    if isinstance(discarded_drafts, list) and discarded_drafts:
        lines.append(
            "- Discarded reviewer drafts: "
            + ", ".join(discarded_drafts)
            + "."
        )

    warnings = state.get("dispatch_plan_warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            lines.append(f"- ⚠ Dispatch warning: {warning}")

    return "\n".join(lines)


def _tri_state(value) -> str:
    """`true`/`false`/`unknown` — never a bare `None` printed as "None"."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _render_record_verdict_line(findings: dict) -> str:
    """The record's closing verdict, stated at the layer that computed it.

    The ledger verdict is the only one derived from findings. The published
    APPROVE/COMMENT/REQUEST_CHANGES layer is shown beside it through the
    module's one mapping, with the single override that can still change it
    named rather than left as a surprise at finalize.
    """
    raw = findings.get("verdict")
    ledger = str(raw).strip().lower() if raw else ""
    published = _LEDGER_TO_REVIEW_VERDICT.get(ledger)
    if not ledger:
        return (
            "**Verdict — from the findings ledger: none recorded.** The "
            "published verdict falls back to COMMENT and the run reports "
            "the absence as a degradation."
        )
    published_clause = (
        f"{published} at the published layer"
        if published
        else "unrecognized at the published layer — the run falls back to "
             "COMMENT and reports it"
    )
    return (
        f"**Verdict — from the findings ledger: `{ledger}` "
        f"({published_clause}).** A critic ESCALATE overrides the published "
        "verdict to COMMENT at finalize; this line reports the ledger, the "
        "only verdict actually computed from findings."
    )


def assemble_review_record(output_dir: str, state: dict) -> tuple:
    """Assemble `review-record.md` from the ledger and the run's own facts.

    Returns ``(outcome, error)`` in the same vocabulary the derived-Markdown
    renders use (``complete`` / ``failed`` with written/expected counts),
    and never raises: a record the pipeline could not assemble is a
    degradation to report, never a reason to abort a step.

    ``expected`` is 0 when there is no ledger — a degraded run that never
    reconciled asked nothing of the assembler, and the step-9 briefing's
    degraded branch routes it to manual synthesis instead.

    Everything here is composed from renderers that already exist:
    ``render_review_body`` for the findings/checks body and
    ``_render_review_accounting_section`` for accounting, both byte-identical
    to what their own callers produce. The record's own new prose is three
    things — its header, the run notes, and the closing verdict line.

    The write is atomic, so a failed assembly leaves the previous record
    intact rather than replacing it with a half-built one.
    """
    findings_path = os.path.join(output_dir, _FINDINGS_JSON)
    expected = 1 if os.path.isfile(findings_path) else 0
    outcome = {
        "ran": True, "written": 0, "expected": expected, "status": "complete",
    }
    if not expected:
        return outcome, None

    read = critic_adjustments.read_findings_file(findings_path)
    if read.status != critic_adjustments.FINDINGS_READ_OK:
        outcome["status"] = "failed"
        return outcome, f"{_FINDINGS_JSON} unreadable ({read.status})"

    findings = read.findings
    try:
        sections = [
            "# Review Record",
            "",
            "*Assembled by the review pipeline from `review-findings.json` "
            "and this run's own measurements. No agent writes or edits this "
            "file — it is the reference the audience-facing report must not "
            "contradict.*",
            "",
            # The one handle this rendering deliberately does not carry.
            # Findings are addressed by a canonical fN ledger `id`, and the
            # renderer this body shares with `review-findings.md` titles
            # each finding rather than numbering it. Saying so here is what
            # keeps a reader — the decision critic above all — from
            # inventing a positional label ("F1") as a key: that exact
            # substitution once failed every adjustment in a REVISE batch
            # with "no finding with id 'F1'".
            "*Findings are keyed by the canonical fN `id` in "
            "`review-findings.json` (`findings[].id`). There are no "
            "positional labels here, and a positional label is not a key "
            "anything can resolve.*",
            "",
            _render_record_body(findings).rstrip("\n"),
            "",
            _render_run_notes(state),
        ]
        accounting = _render_review_accounting_section(
            state.get("review_accounting")
        )
        if accounting:
            sections.extend(["", accounting])
        sections.extend([
            "",
            "---",
            "",
            _render_record_verdict_line(findings),
            "",
        ])
        atomic_write_text(
            os.path.join(output_dir, REVIEW_RECORD_MD), "\n".join(sections)
        )
    except Exception as err:  # noqa: BLE001 — best-effort by design
        outcome["status"] = "failed"
        return outcome, str(err)

    outcome["written"] = 1
    return outcome, None


def _record_review_record(state, outcome):
    """Record the record-assembly outcome AND its degradation flag together.

    The same paired-fact discipline `_record_findings_markdown` follows for
    the findings render, and for the same reason: both assembly seams
    (step 9, step 11) write through here, so a step-9 failure that step 11
    repaired cannot leave a stale flag standing for a later reader.
    """
    state["review_record"] = outcome
    degradation = state.setdefault("degradation", {})
    if outcome["status"] == "complete":
        degradation.pop("review_record_incomplete", None)
        if not degradation:
            state.pop("degradation", None)
    else:
        degradation["review_record_incomplete"] = True


# ---------------------------------------------------------------------------
# Step Orchestration (side effects — subprocesses, file I/O)
# ---------------------------------------------------------------------------

def _dependency_refresh_safety_state():
    """Observe the tracked baseline that gates optional refresh actions."""
    try:
        repo_root = _git_output("rev-parse", "--show-toplevel")
        if not repo_root:
            repo_root = os.getcwd()
    except Exception:
        repo_root = os.getcwd()
    return observe_tracked_worktree(repo_root)


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

def _resolve_current_repo_root():
    """Absolute, symlink-resolved root of the repo containing CWD, or None.

    Repo identity is what makes the step-11 sweep safe: a baseline records
    the repo it measured and the sweep refuses to delete anywhere else.

    Named for the cwd it resolves, to stay distinct from
    ``context._resolve_repo_root(path)`` in this same package, which
    answers the same question for a path it is handed.
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

    Pinned to an explicit root with `git -C`, the same way dependency
    refresh's `observe_tracked_worktree()` pins its own probe,
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
    repo_root = _resolve_current_repo_root()
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
        atomic_write_json(
            os.path.join(output_dir, ".worktree-baseline.json"), payload
        )
    except OSError:
        pass


def _capture_usage_snapshot(output_dir):
    """Capture the run's token usage into `usage-snapshot.json`.

    A subprocess rather than an import on purpose: the measurement lives in
    `scripts/analysis/`, which already depends on `scripts/review/` for the
    telemetry and dispatch-status contracts. Importing it back here would
    close that loop and make the pipeline's finalize path depend on the
    analysis package's import graph.

    Returns the compact summary for `pipeline-result.json`, or None when the
    run has no snapshot to report.
    """
    _run_subprocess(
        [
            sys.executable,
            str(SCRIPTS_DIR.parent / "analysis" / "usage_snapshot.py"),
            "--output-dir", str(output_dir),
        ],
        timeout=USAGE_SNAPSHOT_TIMEOUT,
    )
    return _usage_summary(output_dir)


def _usage_summary(output_dir):
    """Compact token-usage projection for `pipeline-result.json`.

    Built from the manifest section rather than from the raw artifact, so
    both durable surfaces sanitize the snapshot exactly once and cannot
    disagree about what a usable measurement looks like.

    None means the run has no snapshot at all — the capture never ran,
    failed, or wrote something unreadable. A snapshot that ran and measured
    nothing is different: it reports its own "missing" availability with
    null totals, never zeros, so a consumer can tell an unmeasured run from
    a run measured at zero tokens.
    """
    section = manifest_sections.build_usage_manifest(str(output_dir))
    if section is None:
        return None
    totals = section["subagent_totals"] or {}
    counts = section["agents_measured"]

    def count(name):
        value = counts.get(name)
        return value if isinstance(value, int) else "?"

    return {
        "subagent_effective_input": totals.get("effective_input_tokens"),
        "subagent_output": totals.get("output_tokens"),
        "by_model": {
            model: {
                "eff_in": usage["effective_input_tokens"],
                "out": usage["output_tokens"],
            }
            for model, usage in section["usage_by_model"].items()
        },
        "agents_measured": f"{count('measured')}/{count('expected')}",
        # Both halves, never flattened: the subagent number is complete
        # evidence at finalize while the orchestrator's is partial by
        # construction, and a single flag could not say that.
        "availability": section["availability"],
        # The warrant behind "partial": False means the capture substituted
        # its own window bound because the run was still open, which is the
        # normal finalize case. A closed window whose orchestrator half is
        # still partial is the other story — damaged transcript evidence —
        # and a consumer cannot tell them apart without this.
        "window_closed": section["window"]["closed"],
    }


def _check_worktree_hygiene(output_dir):
    """Compare current git status to the step-3 baseline; sweep probe residue.

    Writes and returns worktree-hygiene.json.

    Probe-removal evidence is cumulative within one run directory. Step 11
    has a report-handoff re-entry, and a successful first sweep necessarily
    makes the second observation clean; carrying the prior removed paths is
    what makes this mutating check idempotent at the publication boundary.
    Step 1's stale-artifact sweep removes this file before a new run.

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
    hygiene_path = os.path.join(output_dir, "worktree-hygiene.json")
    prior_removed = []
    prior_baseline_captured_at = None
    try:
        with open(hygiene_path, "r", encoding="utf-8") as f:
            prior = json.load(f)
        if isinstance(prior, dict) and prior.get("schema") == 1:
            removed = prior.get("probe_residue_removed")
            if isinstance(removed, list):
                prior_removed = [
                    path for path in removed
                    if _is_normalized_relative_path(path)
                ]
            captured = prior.get("baseline_captured_at")
            if isinstance(captured, str):
                prior_baseline_captured_at = captured
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    result = {
        "schema": 1,
        "status": "unknown",
        "baseline_captured_at": None,
        "new_files": [],
        "changed_files": [],
        "probe_residue_removed": [],
    }
    baseline = None
    baseline_root = None
    baseline_captured_at = None
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
                captured = data.get("captured_at")
                baseline_captured_at = (
                    captured if isinstance(captured, str) else None
                )
        except (OSError, json.JSONDecodeError):
            baseline = None
            baseline_root = None
            baseline_captured_at = None

    # The identity gate. Everything below — the sweep included — happens
    # only inside the repo the baseline actually measured.
    current_root = _resolve_current_repo_root()
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
            # single "?? newdir/" entry. Git C-quotes any path carrying
            # bytes that need escaping (non-ASCII under core.quotePath's
            # default, control characters, quotes, leading/trailing
            # whitespace), so the printed text is not always the filename:
            # the shared decoder recovers the on-disk name, with
            # surrogateescape because an unlink must address the exact
            # bytes. A malformed quoted line decodes to None and fails
            # closed — reported as an ordinary entry below, never acted on.
            # Unquoted lines pass through byte-identical; in particular no
            # stripping happens, which would remap "probe.go " to a
            # different file.
            remaining = []
            for line in current:
                path, _was_quoted = decode_git_c_quoted_path(
                    line[3:], errors="surrogateescape"
                )
                abs_path = (
                    os.path.join(verified_root, path)
                    if path is not None
                    else None
                )
                if (
                    path is not None
                    and line[:2] == "??"
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
            # Only meaningful once the comparison actually happened: it dates
            # the snapshot the counts are relative to, so a reader looking at
            # an odd number can see how much review the window covers.
            result["baseline_captured_at"] = baseline_captured_at
    # An unverified pair, or a status run that failed, leaves the status at
    # its "unknown" default: with nothing to compare against, reporting
    # "clean" would publish an absent measurement as a measured zero.

    result["probe_residue_removed"] = list(dict.fromkeys(
        prior_removed + result["probe_residue_removed"]
    ))
    if (
        result["baseline_captured_at"] is None
        and result["probe_residue_removed"]
    ):
        result["baseline_captured_at"] = prior_baseline_captured_at

    try:
        # Git paths may contain surrogateescape code points for undecodable
        # bytes. ASCII JSON escapes preserve those path identities while
        # keeping the UTF-8 artifact write valid on every host.
        atomic_write_text(hygiene_path, json.dumps(result, indent=2))
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

    # Trusted-branch dependency refresh — one tracked-state custody gate.
    # The interactive orchestrator decides whether and what to install.
    if config.get("refresh_dependencies"):
        state["dependency_refresh_precheck"] = _dependency_refresh_safety_state()

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
        try:
            report = load_dependency_refresh_report(output_dir)
        except Exception:
            report = None
        state["dependency_refresh_report"] = report

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
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            "reviewer status checker failed — review intake remains open"
        ) from exc

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
            state["waiting_on_agents"]["first_waiting_at"] = previous_waiting[
                "first_waiting_at"
            ]
        else:
            state["waiting_on_agents"]["first_waiting_at"] = datetime.now(
                timezone.utc
            ).isoformat()
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
    elif r.returncode == 0:
        state.pop("waiting_on_agents", None)
    else:
        detail = r.stderr.strip() or r.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            "reviewer status checker failed "
            f"with exit {r.returncode}: {detail}; review intake remains open"
        )

    # Freeze exactly the dispatched reviewer identities before any consumer
    # renders or loads final JSON. Draft saving/finalization and this close
    # share the output-directory lock, so no draft can cross
    # this boundary after the status gate decides to proceed.
    plan_path = os.path.join(output_dir, "dispatch-plan.json")
    dispatch_plan = None
    dispatched_names = []
    if os.path.isfile(plan_path):
        dispatch_plan = _load_dispatch_plan(plan_path)
        dispatched_names = [
            agent["name"] for agent in dispatch_plan["agents"]
            if agent.get("status") in DISPATCHED_STATUSES
        ]
    try:
        intake_close = close_review_intake(output_dir, dispatched_names)
    except Exception as exc:
        raise RuntimeError(
            "review intake could not be closed — reconciliation inputs "
            "are not frozen"
        ) from exc
    invalid_at_close = {
        entry["path"]
        for entry in intake_close.get("invalid_final_reviews", [])
    }
    review_intake = dict(intake_close)
    review_intake.pop("invalid_final_reviews", None)
    state["review_intake"] = review_intake
    discarded_drafts = review_intake["discarded_drafts"]
    degradation = state.setdefault("degradation", {})
    if discarded_drafts:
        degradation["reviewer_drafts_discarded"] = True
    else:
        degradation.pop("reviewer_drafts_discarded", None)
        if not degradation:
            state.pop("degradation", None)

    # Materialize human-facing Markdown from every settled canonical JSON
    # after the status gate confirms readiness or its wait window elapses.
    reviewer_markdown = {
        "ran": True,
        "written": 0,
        "expected": 0,
        "status": "failed",
    }
    materialization_failed = False
    written_markdown = set()
    try:
        written_paths = _materialize_markdown(
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

    if dispatch_plan is not None:
        try:
            review_files = []
            invalid_review_files = []
            completed = []
            output_builder_path = str(SCRIPTS_DIR / "agent" / "output.py")
            for name in dispatched_names:
                reviewer = derive_reviewer_name(name)
                review_file = review_paths(output_dir, reviewer).final
                if os.path.isfile(review_file):
                    if review_file in invalid_at_close:
                        invalid_review_files.append(review_file)
                        continue
                    try:
                        _load_final_review(
                            output_builder_path, review_file, reviewer
                        )
                    except ValueError:
                        invalid_review_files.append(review_file)
                    else:
                        completed.append(name)
                        review_files.append(review_file)
            state["agents"] = {
                "dispatched": dispatched_names,
                "completed": completed,
                "discarded_drafts": discarded_drafts,
                "review_files": review_files,
                "invalid_review_files": invalid_review_files,
            }
        except OSError:
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
    recon_ctx_path = os.path.join(output_dir, "reconciliation-context.json")
    if not ctx_ok or not os.path.isfile(recon_ctx_path):
        raise RuntimeError(
            "reconciliation_context.py failed — cannot proceed to "
            "reconciliation without a valid context file. "
            f"Check stderr above. Expected: {recon_ctx_path}"
        )

    # Dispatch marker for the reconciliator — the last thing this step does
    # before its briefing tells the orchestrator to hand off. The LLM
    # performs the Task call, so the script cannot observe the agent's own
    # boot the way `agent/bootstrap.py` does for reviewers; this is the
    # dispatch-adjacent instant the script DOES own. Placed after the
    # context gate on purpose: a step that raises above never dispatched
    # anything, and a marker there would make a failed setup read as a
    # stalled agent. Best-effort — an unwritable marker costs a
    # measurement, never the review.
    synthesis_lifecycle.mark_dispatched(
        output_dir, synthesis_lifecycle.RECONCILIATOR
    )

    return context


def _orchestrate_step_9(mode, config, state, context, output_dir):
    # First thing this step does: observe how the reconciliator's dispatch
    # ended. Step 9 is the next moment the SCRIPT re-enters after step 8's
    # briefing handed the agent off, so this is the earliest — and
    # therefore tightest — observation the run can take. What it records
    # is a completion, not this moment: `completed_at` in the artifact is
    # review-findings.json's mtime.
    # `finalize=False` — an agent with no artifact here is one this
    # observation caught mid-flight, which is not yet a stall.
    synthesis_lifecycle.observe(output_dir)

    # The step-8 completion path: the reconciliator has published
    # review-findings.json and nothing else. review-findings.md is a
    # mechanical render of that ledger, owned by the pipeline — the report
    # this step is about to brief reads it, the step-10 critic falls back
    # to it, and finalize offers it as the report of last resort. Rendering
    # it here (rather than asking the agent to hand-write this projection) is
    # what makes those three consumers unable to read a stale artifact.
    findings_markdown, render_error = _render_findings_markdown(output_dir)
    if render_error:
        print(
            f"findings markdown materialization failed: {render_error}",
            file=sys.stderr,
        )
    _record_findings_markdown(state, findings_markdown)

    # Load the three inline-coverage populations computed at reconciliation
    # so the report briefing can render them into one paste-ready section:
    # proof gaps, unverified claims, and files no reviewer's scope ever
    # contained. The third is why a file matching no domain (lockfile,
    # binary, dotfile) stops being invisible — it appears in none of the
    # per-agent buckets by construction.
    recon_json_path = os.path.join(output_dir, "reconciliation-context.json")
    review_accounting = None
    if os.path.isfile(recon_json_path):
        try:
            with open(recon_json_path) as f:
                recon = json.load(f)
            review_accounting = review_accounting_from_context(recon)
        except (json.JSONDecodeError, OSError, ValueError):
            review_accounting = None
    state["review_accounting"] = review_accounting

    # Assemble the record LAST, once the coverage populations are in state:
    # the record carries them, and assembling before they were loaded would
    # publish a record whose coverage section is silently empty on a run
    # that had gaps. Same best-effort contract as the findings render — a
    # record the pipeline could not assemble is a degradation the step-9
    # briefing reports, never an exception out of the step.
    record_outcome, record_error = assemble_review_record(output_dir, state)
    if record_error:
        print(
            f"review record assembly failed: {record_error}",
            file=sys.stderr,
        )
    _record_review_record(state, record_outcome)

    return context


def _orchestrate_step_10(mode, config, state, context, output_dir):
    # Observe BEFORE anything else this step does — the same first-thing
    # rule steps 9 and 11 follow — and here it is load-bearing twice over.
    #
    # 1. Step 10 is genuinely re-entered after a COMPLETED critic (the
    #    skip-decision block below says so in as many words: a rerun once
    #    the reconciled verdict escalates). Observing first closes that
    #    attempt's measurement before its outputs are retired below; the
    #    replacement dispatch then becomes the only current attempt.
    #
    # 2. It closes the REVISE window on the RECONCILIATOR. The
    #    orchestrator settles critic adjustments, whose internal applier
    #    updates review-findings.json between step 10 and step 11, so on a
    #    run whose step 9 never
    #    observed, finalize alone would read the apply's mtime and fold
    #    the critic's phase into the reconciliator's duration. Reading
    #    here — before the critic is even dispatched, and on BOTH the
    #    dispatch and skip branches — captures the ledger while its mtime
    #    is still the reconciliator's own completion.
    synthesis_lifecycle.observe(output_dir)

    # Read reconciliation verdict for quick-mode critic skip decision,
    # through the ledger's one shared reader. Inline, this was a fifth
    # spelling of open/parse/use with the narrower `(JSONDecodeError,
    # OSError)` guard and an unconditional `.get()` behind it — so a
    # valid-JSON, non-object ledger (`[1, 2]`, `"hello"`, `5`) escaped the
    # guard and raised AttributeError out of step 10. Exactly the hole
    # `read_verdict_file()` closed for the verdict files, one artifact
    # over. Any state but OK means no usable verdict to read.
    read = critic_adjustments.read_findings_file(
        os.path.join(output_dir, critic_adjustments.FINDINGS_FILENAME)
    )
    if read.status == critic_adjustments.FINDINGS_READ_OK:
        state["reconciliation_verdict"] = read.findings.get("verdict", "")
    elif read.status != critic_adjustments.FINDINGS_READ_ABSENT:
        state["reconciliation_verdict"] = ""

    # Which artifact the decision critic can actually be pointed at.
    # briefings.py is pure, so the filesystem question is answered here and
    # travels in state — the same division that already puts
    # `reconciliation_verdict` above rather than re-reading the ledger in
    # the briefing. Recording `available` alongside the choice keeps the
    # briefing from having to re-derive why it got the target it got.
    available = [
        name for name in _CRITIC_SOURCE_CANDIDATES
        if os.path.isfile(os.path.join(output_dir, name))
    ]
    state["critic_source"] = {
        "target": available[0] if available else None,
        "available": available,
        "render_incomplete": bool(
            state.get("degradation", {}).get("findings_markdown_incomplete")
        ),
    }

    # Record critic skip decision for telemetry.
    # Clear any stale decision first (step 10 may be rerun after
    # review-findings.json changes from approve/comment to a higher verdict).
    state.setdefault("step_decisions", {}).pop("10", None)
    is_quick = config.get("quick", False)
    recon_verdict = state.get("reconciliation_verdict", "")
    should_skip = (
        is_quick and recon_verdict.lower() in ("approve", "comment")
    )
    if should_skip:
        reason = f"quick mode + reconciliation verdict: {recon_verdict}"
        state["step_decisions"]["10"] = {
            "critic_skipped": True,
            "reason": reason,
        }
        # The PIPELINE records its own skip. This used to be an instruction
        # in the step-10 briefing — the orchestrator was told to transcribe
        # a verdict for a decision the pipeline had already made — so a run
        # whose orchestrator stopped short left no verdict artifact at all,
        # indistinguishable at finalize from a critic that ran and crashed.
        # A fact the pipeline knows is a fact the pipeline writes, and that
        # separation is what lets finalize read a missing artifact beside a
        # dispatch marker as the real degradation it is.
        proposal = critic_adjustments.prepare_proposal({
            "schema": critic_adjustments.ADJUSTMENTS_SCHEMA,
            "adjustments": [],
        })
        digest = critic_adjustments.proposal_digest(proposal)

    # A step-10 re-entry starts a new critic decision, including the
    # pipeline-owned quick skip. Retire the whole prior attempt under the
    # same lock as the next marker/snapshot so a failed replacement cannot
    # leave an old verdict readable as the new result.
    with atomic_io.output_dir_lock(output_dir):
        for filename in _CRITIC_OUTPUT_FILENAMES:
            try:
                os.unlink(os.path.join(output_dir, filename))
            except FileNotFoundError:
                pass
        try:
            os.unlink(synthesis_lifecycle.marker_path(
                output_dir, synthesis_lifecycle.DECISION_CRITIC
            ))
        except FileNotFoundError:
            pass

        if should_skip:
            critic_adjustments.write_adjustments(output_dir, proposal)
            atomic_write_json(os.path.join(
                output_dir, critic_adjustments.CRITIC_VERDICT_FILENAME
            ), {
                "schema": critic_adjustments.VERDICT_MARKER_SCHEMA,
                "verdict": "SKIPPED",
                "proposal_digest": digest,
            })
        else:
            synthesis_lifecycle.mark_dispatched(
                output_dir, synthesis_lifecycle.DECISION_CRITIC
            )

    return context


_STEP_11_DEGRADATION_CODES = frozenset({
    "critic_adjudication_missing",
    "critic_unavailable_after_dispatch",
    "critic_adjustment_apply_failed",
    "critic_adjustment_apply_refused",
    "critic_adjustment_inspection_failed",
    "critic_adjustment_pending_non_revise",
    "findings_markdown_render_failed",
    "review_record_assembly_failed",
    "probe_residue_swept",
    "findings_missing",
    "ledger_verdict_unusable",
})
_PROBE_RESIDUE_DISCRIMINATOR_PREFIX = "paths-sha256:"


def _degradation_identity(record):
    return record["code"], record.get("discriminator")


def _valid_degradation_discriminator(code, discriminator):
    """Accept only the discriminator shape owned by one known producer."""
    if code != "probe_residue_swept":
        return discriminator is None
    if not isinstance(discriminator, str) or not discriminator.startswith(
        _PROBE_RESIDUE_DISCRIMINATOR_PREFIX
    ):
        return False
    return _valid_sha256(
        discriminator[len(_PROBE_RESIDUE_DISCRIMINATOR_PREFIX):]
    )


def _record_step_11_degradation(
    records, code, message, discriminator=None,
):
    """Record one degradation by stable producer identity, first message."""
    if not isinstance(code, str) or code not in _STEP_11_DEGRADATION_CODES:
        raise ValueError(f"unknown step-11 degradation code: {code}")
    if not isinstance(message, str) or not message:
        raise ValueError("step-11 degradation message must be non-empty")
    if not _valid_degradation_discriminator(code, discriminator):
        raise ValueError("invalid step-11 degradation discriminator")
    identity = (code, discriminator)
    record = {"code": code, "message": message}
    if discriminator is not None:
        record["discriminator"] = discriminator

    # Probe residue is one cumulative fact, not one event per finalize pass.
    # Replace it in place as the swept set grows so public prose reports only
    # the current total and its position among other degradation facts stays
    # stable across re-entry.
    if code == "probe_residue_swept":
        matching = [
            index for index, existing in enumerate(records)
            if existing.get("code") == code
        ]
        if matching:
            records[matching[0]] = record
            for index in reversed(matching[1:]):
                del records[index]
            return

    if any(_degradation_identity(record) == identity for record in records):
        return
    records.append(record)


def _valid_degradation_records(value):
    """Validate the private record collection as one provenance unit."""
    if not isinstance(value, list):
        return []
    for record in value:
        if not isinstance(record, dict):
            return []
        if set(record) - {"code", "message", "discriminator"}:
            return []
        if not (
            isinstance(record.get("code"), str)
            and record["code"] in _STEP_11_DEGRADATION_CODES
            and isinstance(record.get("message"), str) and record["message"]
        ):
            return []
        if not _valid_degradation_discriminator(
            record["code"], record.get("discriminator")
        ):
            return []
    return value


def _merge_step_11_degradation_records(state, current_records):
    """Carry step-11 degradations across handoff by stable identity.

    The public ``degradation_notes`` and the legacy private string list are
    presentation prose, not provenance, so neither is an inheritance source.
    A malformed private record collection is ignored as a whole.
    """
    prior_records = _valid_degradation_records(
        state.get("step_11_degradation_records")
    )
    merged = []
    for record in [*prior_records, *current_records]:
        _record_step_11_degradation(
            merged,
            record["code"],
            record["message"],
            record.get("discriminator"),
        )
    return merged


def _degradation_messages(records):
    return [record["message"] for record in records]


def _degradation_identities(records):
    identities = []
    for record in records:
        identity = {"code": record["code"]}
        if record.get("discriminator") is not None:
            identity["discriminator"] = record["discriminator"]
        identities.append(identity)
    return identities


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _probe_residue_provenance(paths):
    """Return stable private provenance for cumulative swept repo paths."""
    normalized = []
    for path in paths:
        if not _is_normalized_relative_path(path):
            raise ValueError("probe residue provenance requires normalized paths")
        normalized.append(path)
    normalized = sorted(set(normalized))
    encoded = json.dumps(
        normalized, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "count": len(normalized),
        "discriminator": (
            _PROBE_RESIDUE_DISCRIMINATOR_PREFIX + _sha256_bytes(encoded)
        ),
    }


def _artifact_source_identity(path):
    """Return a content identity that distinguishes absence from bytes."""
    try:
        with open(path, "rb") as artifact:
            payload = artifact.read()
    except FileNotFoundError:
        return {"status": "absent", "sha256": None}
    except OSError:
        return {"status": "unreadable", "sha256": None}
    return {"status": "present", "sha256": _sha256_bytes(payload)}


def _report_source_fingerprint(
    output_dir, findings_status, status, verdict, verdict_source,
    critic_verdict, degradation_records,
):
    """Bind source bytes to settled facts and stable degradation identities."""
    source = {
        "review_record": _artifact_source_identity(
            os.path.join(output_dir, REVIEW_RECORD_MD)
        ),
        "review_findings": {
            **_artifact_source_identity(
                os.path.join(output_dir, _FINDINGS_JSON)
            ),
            "read_status": findings_status,
        },
        "terminal_facts": {
            "status": status,
            "verdict": verdict,
            "verdict_source": verdict_source,
            "critic_verdict": critic_verdict,
            "degradation_identities": _degradation_identities(
                degradation_records
            ),
        },
    }
    encoded = json.dumps(
        source, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _valid_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _bind_report_handoff(state, report_path, source_fingerprint):
    """Decide whether this report is bound to the current settlement."""
    prepared = state.get("prepared_report_source_fingerprint")
    if not _valid_sha256(prepared):
        prepared = None

    report_identity = _artifact_source_identity(report_path)
    report_digest = report_identity["sha256"]
    stale_digest = state.get("stale_report_digest")
    if not _valid_sha256(stale_digest):
        stale_digest = None
        state.pop("stale_report_digest", None)

    if report_digest is None:
        state["prepared_report_source_fingerprint"] = source_fingerprint
        state["report_handoff_status"] = (
            "report_missing"
            if report_identity["status"] == "absent"
            else "report_unreadable"
        )
        return True

    if prepared is None:
        state["prepared_report_source_fingerprint"] = source_fingerprint
        state["stale_report_digest"] = report_digest
        state["report_handoff_status"] = "unbound_report"
        return True

    if prepared != source_fingerprint:
        state["prepared_report_source_fingerprint"] = source_fingerprint
        state["stale_report_digest"] = report_digest
        state["report_handoff_status"] = "source_changed"
        return True

    if stale_digest == report_digest:
        state["report_handoff_status"] = "stale_report_unchanged"
        return True

    state.pop("stale_report_digest", None)
    state["report_handoff_status"] = "published"
    return False


def _orchestrate_step_11(mode, config, state, context, output_dir):
    # Synthesis-agent lifecycle, adjudicated FIRST and for a hard ordering
    # reason: finalize itself may recover an apply to review-findings.json,
    # and that write moves the mtime this measurement
    # reads as the reconciliator's
    # completion. Observing after that write would report the
    # reconciliator as having finished at finalize time — the run's whole
    # wall clock instead of its synthesis phase.
    #
    # The guarantee is a chain, not this one placement. Steps 9, 10, and
    # 11 each observe before doing anything else, and every observation
    # carries an already-completed row forward verbatim, so the recorded
    # completion is always the EARLIEST evidence any step saw:
    #   step 9  — reads review-findings.json straight out of the
    #             reconciliator's own write.
    #   step 10 — reads it again before the critic is dispatched, which
    #             is what covers a run whose step 9 never observed: the
    #             orchestrator's REVISE settlement lands on that ledger
    #             between step 10 and here, and without step 10's
    #             reading finalize would fold the critic's phase into the
    #             reconciliator's duration.
    #   step 11 — this call, which adds the critic's own completion and
    #             is the last chance to observe before finalize writes.
    #
    # `finalize=True` is the stall adjudication: both agents run in the
    # orchestrator's foreground, so nothing here can interrupt one — the
    # policy is report, never kill. A marker with no completion artifact
    # at this point records `stalled: true` and how long the stall had
    # lasted, which is the artifact a hung run previously never produced.
    synthesis_lifecycle.observe(output_dir, finalize=True)

    # Read critic verdict from file (written by the orchestrator at step 10
    # when the critic ran, by the pipeline itself on the quick skip), through
    # critic_adjustments.py's own presentation wrapper — one parser and
    # one SKIPPED/missing → "unavailable" mapping, shared with (and kept
    # in sync with) the raw reader apply_adjustments()'s gate uses.
    state["critic_verdict"] = critic_adjustments.critic_verdict_for_state(
        output_dir
    )

    critic_verdict = state["critic_verdict"]

    findings_path = os.path.join(output_dir, "review-findings.json")
    degradation_records = []

    # Critic-absence honesty, keyed on the DISPATCH MARKER and the usable
    # verdict — never on whether some file exists.
    #
    # `critic_verdict_for_state()` collapses a missing verdict file and an
    # explicit SKIPPED into "unavailable": the right presentation for
    # pirategoat-bot, which shows either as "not cross-validated", but it
    # cannot tell a critic that was never dispatched from one that was
    # dispatched and said nothing usable. The marker is what separates
    # them, and only the second is a degradation — quick mode skipping the
    # critic is the pipeline working as designed (its SKIPPED record is
    # pipeline-written, and that branch writes no marker), while a
    # dispatched critic with no usable verdict is a run that lost its
    # stress test.
    #
    # Checking `critic_verdict` rather than the artifact's existence is
    # what makes this honest in BOTH directions. An artifact check degrades
    # the orchestrator that stopped short and stays silent for the one that
    # dutifully recorded a SKIPPED stand-in for a crashed critic — rewarding
    # the compliant run for hiding the same lost stress test. Both rows of
    # that table are the same fact, so both land here. (The step-10
    # briefing no longer asks for that stand-in either.)
    critic_dispatched = os.path.isfile(synthesis_lifecycle.marker_path(
        output_dir, synthesis_lifecycle.DECISION_CRITIC
    ))
    if critic_dispatched and critic_verdict == "unavailable":
        _record_step_11_degradation(
            degradation_records,
            "critic_unavailable_after_dispatch",
            "critic was dispatched but produced no verdict"
        )

    # Carry any pending finding/check critic adjustments into the ledger
    # before the verdict is derived from it — but only under REVISE, the one
    # that sanctions them. The step-10 REVISE briefing has the orchestrator
    # spot-check each entry and submit only positive verified/refuted claims
    # through settle, so here it is the defensive re-run: any
    # orchestrator — bot or interactive — can stop short of the step-10
    # briefing's instructions (a crash, an early return, a main
    # orchestrator that skips ahead), and this re-run is what still
    # converges those runs on a findings JSON the critic actually reached.
    # Idempotence makes the re-run free for a run that already applied.
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
        if critic_verdict == "REVISE":
            try:
                apply_result = critic_adjustments.apply_adjustments(output_dir)
            except (ValueError, OSError, json.JSONDecodeError) as err:
                _record_step_11_degradation(
                    degradation_records,
                    "critic_adjustment_apply_failed",
                    f"critic adjustment apply attempt failed: {err}"
                )
            else:
                # Belt-and-braces: `critic_verdict` above came from
                # critic_verdict_for_state()'s presentation mapping, and
                # this branch only runs when that read already says
                # REVISE, so apply_adjustments()'s own gate — reading the
                # same file through read_critic_verdict() — should never
                # refuse here. It would if a future edit changed one
                # mapping (e.g. what "SKIPPED" or a new alias verdict
                # means) without changing the other, so this branch exists
                # to catch exactly that divergence and degrade loudly
                # instead of silently doing nothing.
                if apply_result.get("status") == "refused":
                    _record_step_11_degradation(
                        degradation_records,
                        "critic_adjustment_apply_refused",
                        f"critic adjustment apply attempt refused: "
                        f"({apply_result.get('reason')})"
                    )
                elif apply_result.get("adjudication_source") == (
                    critic_adjustments.ADJUDICATION_SOURCE_DEFENSIVE
                ):
                    _record_step_11_degradation(
                        degradation_records,
                        "critic_adjudication_missing",
                        "critic adjustments were applied without "
                        "orchestrator adjudication",
                    )
        else:
            try:
                pending = critic_adjustments.pending_count(output_dir)
            except (ValueError, OSError, json.JSONDecodeError) as err:
                _record_step_11_degradation(
                    degradation_records,
                    "critic_adjustment_inspection_failed",
                    f"critic adjustment inspection failed: {err}"
                )
            else:
                if pending:
                    _record_step_11_degradation(
                        degradation_records,
                        "critic_adjustment_pending_non_revise",
                        f"critic adjustment apply skipped on this settlement "
                        f"pass: critic verdict was {critic_verdict} "
                        f"(adjustments are a REVISE-only channel)"
                    )

    # Re-render the derived artifacts from the FINAL ledger — immediately
    # after the adjustments landed and before anything else reads them, so
    # every downstream consumer in this function and in step 11's briefing
    # sees the ledger the run actually publishes. This is the seam that
    # closes the field-proven staleness: every critic REVISE used to leave
    # the hand-written Markdown showing pre-adjustment severities while
    # the JSON showed post-adjustment ones.
    #
    # `review-record.md` matters most here. It is what the step-11 briefing
    # tells the orchestrator to author the report from, and the report is
    # the run's whole audience-facing output — a record still describing
    # the pre-critic ledger would be a post-critic report built on
    # pre-critic facts.
    #
    # Both are best-effort by construction: a render failure is a
    # degradation note, never an exception out of finalize, and never a
    # faked file.
    findings_markdown, render_error = _render_findings_markdown(output_dir)
    _record_findings_markdown(state, findings_markdown)
    if render_error:
        _record_step_11_degradation(
            degradation_records,
            "findings_markdown_render_failed",
            f"review-findings.md render failed: {render_error}"
        )
    record_outcome, record_error = assemble_review_record(output_dir, state)
    _record_review_record(state, record_outcome)
    if record_error:
        _record_step_11_degradation(
            degradation_records,
            "review_record_assembly_failed",
            f"{REVIEW_RECORD_MD} assembly failed: {record_error}"
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
            "baseline_captured_at": hygiene.get("baseline_captured_at"),
        }
    # Only the sweep degrades the run. A requester editing their own tree
    # during a review is routine, and `status` is a bot contract that has to
    # keep meaning "the review pipeline underperformed" — spending it on
    # someone else's ordinary keystrokes would teach every consumer to
    # ignore it. Swept residue is different in kind: it is a pipeline
    # participant breaking the rule that a probe is created, run, and
    # deleted by the same command.
    if hygiene.get("probe_residue_removed"):
        probe_provenance = _probe_residue_provenance(
            hygiene["probe_residue_removed"]
        )
        _record_step_11_degradation(
            degradation_records,
            "probe_residue_swept",
            f"probe residue swept at finalize: "
            f"{probe_provenance['count']} file(s) — a probe should be "
            "deleted in the same command that created it",
            probe_provenance["discriminator"],
        )
    # "unknown" is silent by construction now: with no verified baseline
    # nothing was swept and nothing was compared, so there is no outcome to
    # report — only the absence of one, which `worktree_hygiene: null` here,
    # the hygiene artifact, and the run manifest's `worktree_hygiene` section
    # already state.

    # What the review cost, captured while the evidence still exists. Every
    # subagent transcript is closed by now, so their usage is completely
    # measurable; the orchestrator is measuring its own still-open session
    # and says so. A failed or absent capture is silent for the same reason
    # hygiene "unknown" is: a Codex host writes no Claude-format transcripts
    # at all, and so does every run older than this feature, so a
    # legacy-normal absence must never spend the `status` field.
    usage_summary = _capture_usage_snapshot(output_dir)

    # The audience-facing report is the terminal handoff, not a best-effort
    # projection. On the first pass it is expected to be absent: settlement
    # above prepares the state and the briefing asks the orchestrator to
    # author it. Only a re-entered step 11 may publish the terminal result,
    # and that result points at this exact file — never review-record.md or
    # review-findings.md as a fallback. pirategoat-bot treats the result's
    # existence as "complete" during resume discovery and then reads the
    # report verbatim, so publishing those two facts separately creates an
    # unrecoverable run.
    report_path = os.path.join(output_dir, "review-report.md")
    if not os.path.isfile(findings_path):
        _record_step_11_degradation(
            degradation_records,
            "findings_missing",
            "review-findings.json was absent during step 11 settlement"
        )

    # The published verdict is DERIVED from the findings ledger, not
    # transcribed. It used to travel LLM → review-verdict.json → here, with
    # a Rule 23 sync writing it back over the ledger's own verdict; a run
    # whose orchestrator wrote COMMENT above a ledger holding a critical
    # finding published COMMENT, and the sync then made the ledger agree
    # with the transcription rather than the other way round. Deriving
    # removes the transcription step entirely: the ledger is the only
    # artifact whose verdict any reviewer, reconciliator, or critic batch
    # actually computed, and `critic_adjustments.apply_adjustments()`
    # recomputes it after every applying batch, so it is current by the
    # time finalize reads it. `_LEDGER_TO_REVIEW_VERDICT` (module scope) is
    # the one place the two verdict layers meet.
    ledger_verdict = None
    findings_read = critic_adjustments.read_findings_file(findings_path)
    raw = (findings_read.findings or {}).get("verdict") if (
        findings_read.status == critic_adjustments.FINDINGS_READ_OK
    ) else None
    if raw:
        ledger_verdict = _LEDGER_TO_REVIEW_VERDICT.get(
            str(raw).strip().lower()
        )

    if critic_verdict == "ESCALATE":
        # The critic's one unilateral power, exercised by the pipeline
        # rather than asked of the orchestrator: ESCALATE means the review's
        # conclusions did not survive the stress test, so nothing it
        # concluded is strong enough to gate a merge.
        verdict = "COMMENT"
        verdict_source = "critic ESCALATE override"
    elif ledger_verdict is not None:
        verdict = ledger_verdict
        verdict_source = "findings ledger"
    else:
        verdict = "COMMENT"
        verdict_source = "fallback: no usable ledger verdict"
        _record_step_11_degradation(
            degradation_records,
            "ledger_verdict_unusable",
            "no usable verdict in review-findings.json — verdict fell "
            "back to COMMENT"
        )

    # Computed last: every degradation any of the work above discovered has
    # to reach the status it is reported beside, or the run publishes
    # "success" while carrying a note that says otherwise. Step 11's report
    # handoff makes this function re-entrant, so merge its own prior records
    # by producer identity before deriving status: a transient prepare-pass
    # failure remains part of the terminal run even when publication can
    # repeat that work cleanly, while changing diagnostics cannot multiply
    # one event or destabilize the source fingerprint.
    degradation_records = _merge_step_11_degradation_records(
        state, degradation_records
    )
    degradation_notes = _degradation_messages(degradation_records)
    status = "success" if not degradation_records else "degraded"
    source_fingerprint = _report_source_fingerprint(
        output_dir,
        findings_read.status,
        status,
        verdict,
        verdict_source,
        critic_verdict,
        degradation_records,
    )
    publication_pending = _bind_report_handoff(
        state, report_path, source_fingerprint
    )

    pipeline_result = {
        "status": status,
        "verdict": verdict,
        "report_path": report_path,
        "findings_path": findings_path if os.path.isfile(findings_path) else None,
        "critic_verdict": critic_verdict,
        "degradation_notes": degradation_notes,
        "review_baseline_saved": os.path.isfile(
            os.path.join(output_dir, ".branch-review-baseline.json")
        ),
        "worktree_hygiene": hygiene_summary,
        "usage": usage_summary,
        # Which of the three derivation branches produced `verdict`. Not a
        # closed vocabulary a consumer branches on — it is the audit line
        # that says whether the published verdict came from the ledger, from
        # the critic's override, or from the fallback the degradation note
        # beside it already explains.
        "verdict_source": verdict_source,
    }
    result_path = os.path.join(output_dir, "pipeline-result.json")

    state["verdict"] = verdict
    state["review_verdict"] = verdict
    state["pipeline_status"] = status
    # Carried into state so step 11's pure briefing can describe prepared
    # settlement on pass one and terminal publication on pass two without
    # re-reading pipeline-result.json — the same division that already puts
    # `critic_source` here.
    state["verdict_source"] = verdict_source
    state["degradation_notes"] = list(degradation_notes)
    state["step_11_degradation_records"] = [
        dict(record) for record in degradation_records
    ]
    # Migration from the immediately preceding string-only private state:
    # those messages carry no trustworthy producer identity, so never reuse
    # them as either audit facts or fingerprint input.
    state.pop("step_11_degradation_notes", None)
    state["publication_pending"] = publication_pending

    if publication_pending:
        # Keep the terminal-marker invariant true even if a human deletes
        # the report and explicitly re-enters this step after an earlier
        # publication. The normal prepare pass has no result to remove.
        try:
            os.remove(result_path)
        except FileNotFoundError:
            pass
        return context

    atomic_write_json(result_path, pipeline_result)

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

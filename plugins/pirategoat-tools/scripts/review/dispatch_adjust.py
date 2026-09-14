#!/usr/bin/env python3
"""Record the orchestrator's dispatch-plan adjustments.

This is the orchestrator's one channel for the step-5 adjustments; the plan
is never edited by hand or by a per-run script. `--skip NAME REASON` moves a
dispatched agent to SKIPPED_OVERRIDE and `--dispatch NAME REASON` moves a
skipped one to DISPATCH_OVERRIDE; both repeatable. Every name is validated
against the plan first, and the read and the single atomic write happen
under the run-directory lock. An unknown name, an empty reason or a
malformed plan exits 1 and writes nothing. A request that moves no status is
never refused: an agent already in the requested family is a reported
`UNCHANGED` no-op, and a new reason on an override already in place is a
reported `UPDATED` reason, so a re-run is idempotent. Two transitions are
refused because they cannot do what they say: a `--dispatch` of a
`no_domain_files` skip (bootstrap scopes the agent to the same empty domain,
and no review comes of it) and a `--skip` of a dispatched agent whose
started marker or final review exists (a skipped row is no longer waited
for, so the skip only hides a live reviewer); each refusal names the route
that works.

`dispatch_status.OVERRIDE_REASON_KEY`, `PLANNER_STATUS_KEY` and
`ORPHANED_FILES_KEY` are the one spelling of the fields written here. Each
skip's orphaned files come from `plan_dispatch.scope_files`, the derivation
the planner's own triage uses.

Usage:
    dispatch_adjust.py --output-dir DIR --skip a11y-reviewer "no markup in the diff"
                       --skip security-reviewer "no input, escaping or auth surface"
                       [--dispatch woo-regression-reviewer "..."] [--dry-run]
"""

import argparse
import json
import os
import sys

try:
    from . import atomic_io
    from .dispatch_status import (
        DISPATCH_OVERRIDE, DISPATCHED_STATUSES, ORPHANED_FILES_KEY, ORPHANED_FILES_LEAD,
        OVERRIDE_REASON_KEY, PLANNER_STATUS_KEY, SIGNAL_NO_DOMAIN_FILES, SKIPPED_OVERRIDE,
        SKIPPED_STATUSES, load_dispatch_plan,
    )
    from .reviewer_lifecycle import review_paths, started_marker_path
    from .reviewer_names import derive_reviewer_name
    from .plan_dispatch import scope_files
    from .review_document import normalize_bounded_text
    from .run_paths import artifact_path
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import atomic_io
    from review.dispatch_status import (
        DISPATCH_OVERRIDE, DISPATCHED_STATUSES, ORPHANED_FILES_KEY, ORPHANED_FILES_LEAD,
        OVERRIDE_REASON_KEY, PLANNER_STATUS_KEY, SIGNAL_NO_DOMAIN_FILES, SKIPPED_OVERRIDE,
        SKIPPED_STATUSES, load_dispatch_plan,
    )
    from review.reviewer_lifecycle import review_paths, started_marker_path
    from review.reviewer_names import derive_reviewer_name
    from review.plan_dispatch import scope_files
    from review.review_document import normalize_bounded_text
    from review.run_paths import artifact_path

ACTION_SKIP = "skip"
ACTION_DISPATCH = "dispatch"
# What each flag may do: the statuses it moves a row from, and the one it sets.
_TRANSITIONS = {
    ACTION_SKIP: (DISPATCHED_STATUSES, SKIPPED_OVERRIDE),
    ACTION_DISPATCH: (SKIPPED_STATUSES, DISPATCH_OVERRIDE),
}


class DispatchAdjustmentError(ValueError):
    """Every problem with a request, so the caller fixes them in one turn."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def _load_plan(output_dir):
    path = artifact_path(output_dir, "dispatch_plan")
    try:
        return path, load_dispatch_plan(path)
    except FileNotFoundError:
        raise ValueError(
            f"no dispatch plan under {output_dir}: run pipeline step 5 first "
            f"(it writes {path.name})"
        ) from None


def _requests(skips, dispatches):
    """Normalise `(name, reason)` pairs into one adjustment per agent."""
    problems = []
    requests = {}
    for action, pairs in ((ACTION_SKIP, skips or []), (ACTION_DISPATCH, dispatches or [])):
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                problems.append(f"--{action} takes an agent name and a reason")
                continue
            name, reason = pair
            if not isinstance(name, str) or not name.strip():
                problems.append(f"--{action} needs a non-empty agent name")
                continue
            name = name.strip()
            try:
                reason = normalize_bounded_text(reason, f"--{action} {name} reason")
            except ValueError as err:
                problems.append(str(err))
                continue
            if name in requests:
                problems.append(f"'{name}' is named twice; one adjustment per agent")
                continue
            requests[name] = (action, reason)
    if not requests and not problems:
        problems.append("nothing to adjust: pass --skip NAME REASON and/or --dispatch NAME REASON")
    return requests, problems


def _scope_files(files, agent):
    """The changed files a plan row's scope covers: its declared domains
    (a registry row's primary plus secondary domains, a repo reviewer's
    `scope_domains`) and, for a repo reviewer, its `applies_to.paths`."""
    domains = agent.get("scope_domains") or ([agent["domain"]] if agent.get("domain") else [])
    return set(scope_files(files, domains, agent.get("include_paths") or []))


def _record_override_orphans(plan):
    """Stamp each override-skipped agent with the changed files its scope
    alone covered, and clear the key everywhere else.

    A file whose only matching reviewer was skipped is reviewed by no one,
    and the orchestrator should see that when it decides and the coverage
    section should say why afterwards. The list is recomputed on every call
    from the plan's current statuses, so a later dispatch that covers a
    file clears it; a plan without its changed-file list records nothing,
    since nothing was measured.
    """
    files = plan.get("changed_files")
    agents = plan["agents"]
    if not isinstance(files, list):
        for agent in agents:
            agent.pop(ORPHANED_FILES_KEY, None)
        return
    covered = set()
    for agent in agents:
        if agent["status"] in DISPATCHED_STATUSES:
            covered.update(_scope_files(files, agent))
    for agent in agents:
        if agent["status"] == SKIPPED_OVERRIDE:
            agent[ORPHANED_FILES_KEY] = sorted(_scope_files(files, agent) - covered)
        else:
            agent.pop(ORPHANED_FILES_KEY, None)


def _refusal(output_dir, agent, action):
    """Why this transition cannot do what it says, or None.

    Judged only for a request that moves a row from the flag's source
    statuses to its target, since that move is what each refusal protects:
    a repeat, a new reason on an override already in place, or a row the
    planner already put where the flag points moves no status, so a re-run
    is never refused.

    A `no_domain_files` skip is a scope fact, not a triage judgment:
    bootstrap scopes the agent to the same domain the planner measured,
    and finds nothing, so the dispatch buys a subagent spawn and no
    review (five cohort attempts, zero findings). A started agent cannot be un-dispatched: agents_status
    stops waiting for a skipped row, so the skip only hides a reviewer
    that is still running or has already finished.
    """
    name = agent["name"]
    if action == ACTION_DISPATCH and agent.get("signal") == SIGNAL_NO_DOMAIN_FILES:
        return (
            f"{name} has no files in its domain (signal {SIGNAL_NO_DOMAIN_FILES}): "
            "a forced dispatch finds an empty scope and produces no review. A claim "
            "you want checked against the code is a step-8 note "
            "(reconciliation_notes.py --note); a file no dispatched domain covers "
            "is a repo reviewer with applies_to.paths"
        )
    if action == ACTION_SKIP:
        reviewer = derive_reviewer_name(name)
        if os.path.exists(review_paths(str(output_dir), reviewer).final):
            return (
                f"{name} has already started and finished; its review stands. "
                "To contest it, register a step-8 note (reconciliation_notes.py "
                "--note) for the reconciliator to weigh"
            )
        if os.path.exists(started_marker_path(str(output_dir), reviewer)):
            return (
                f"{name} has already started: a skipped row is no longer waited "
                "for, so the skip would hide a running reviewer. Wait for it "
                "(agents_status.py) or let it time out"
            )
    return None


def adjust_dispatch_plan(output_dir, skips=None, dispatches=None, dry_run=False):
    """Apply the adjustments to the run's dispatch plan.

    Returns `{"adjustments": [...], "dispatching": n, "skipped": m,
    "written": bool}`; each adjustment row is `{"name", "from", "to",
    "reason", "changed"}`. An agent already in the requested family — a
    `--skip` of a planner-skipped agent, a `--dispatch` of a dispatched
    one, or a repeat of the same override — is a reported no-op
    (`changed: False`), never a refusal, so a re-run after the planner
    changed its mind is idempotent. A new reason on an override already in
    place is recorded (`changed: True`, `from` equal to `to`) and never
    refused, since no status moves. Raises DispatchAdjustmentError with
    every problem and writes nothing when any request is invalid.
    """
    requests, problems = _requests(skips, dispatches)
    if problems:
        raise DispatchAdjustmentError(problems)
    with atomic_io.output_dir_lock(str(output_dir)):
        path, plan = _load_plan(output_dir)
        agents = plan["agents"]
        by_name = {agent["name"]: agent for agent in agents}
        known = ", ".join(sorted(by_name))
        rows = []
        for name, (action, reason) in requests.items():
            agent = by_name.get(name)
            if agent is None:
                problems.append(f"unknown agent '{name}'; the plan names: {known}")
                continue
            sources, target = _TRANSITIONS[action]
            current = agent["status"]
            if current == target:
                to, changed = target, agent.get(OVERRIDE_REASON_KEY) != reason
            elif current in sources:
                refusal = _refusal(output_dir, agent, action)
                if refusal:
                    problems.append(refusal)
                    continue
                to, changed = target, True
            else:
                # Already skipped (or dispatched) by the planner: nothing to
                # override, and the other flag would flip it the wrong way.
                to, changed = current, False
            rows.append({"name": name, "from": current, "to": to, "reason": reason,
                         "changed": changed})
        if problems:
            raise DispatchAdjustmentError(problems)

        before = json.dumps(plan, sort_keys=True)
        for row in rows:
            if row["changed"]:
                agent = by_name[row["name"]]
                agent.setdefault(PLANNER_STATUS_KEY, row["from"])
                agent["status"] = row["to"]
                agent[OVERRIDE_REASON_KEY] = row["reason"]
        _record_override_orphans(plan)
        for row in rows:
            orphans = by_name[row["name"]].get(ORPHANED_FILES_KEY)
            row[ORPHANED_FILES_KEY] = list(orphans) if orphans is not None else None
        dispatching = sum(1 for agent in agents if agent["status"] in DISPATCHED_STATUSES)
        skipped = sum(1 for agent in agents if agent["status"] in SKIPPED_STATUSES)
        written = False
        # The orphan record is part of the plan's truth: a call that changes
        # nothing else but stamps it (the first after a skip recorded
        # without it) still writes.
        if not dry_run and json.dumps(plan, sort_keys=True) != before:
            atomic_io.atomic_write_json(path, plan)
            written = True
    return {"adjustments": rows, "dispatching": dispatching, "skipped": skipped, "written": written}


def render_adjustments(rows):
    """One line per adjustment, the form the step-6 briefing repeats."""
    lines = []
    for row in rows:
        if row["changed"]:
            if row["from"] == row["to"]:
                # A new reason on an override already in place: no status
                # moved, so the line must not read like a fresh override.
                lines.append(
                    f"UPDATED {row['name']} — already {row['to']}, reason now: {row['reason']}"
                )
            else:
                lines.append(f"{row['to']} {row['name']} — {row['reason']}")
            if row.get(ORPHANED_FILES_KEY):
                lines.append(
                    "  " + ORPHANED_FILES_LEAD
                    + ", ".join(f"`{path}`" for path in row[ORPHANED_FILES_KEY])
                )
        elif row["from"] == row["to"] and row["to"] in (SKIPPED_OVERRIDE, DISPATCH_OVERRIDE):
            lines.append(f"UNCHANGED {row['name']} — already {row['to']} for this reason")
        else:
            verb = "skipped" if row["from"] in SKIPPED_STATUSES else "dispatched"
            lines.append(f"UNCHANGED {row['name']} — already {verb} by the planner ({row['from']})")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record the orchestrator's dispatch-plan adjustments",
        epilog=(
            "Exit codes: 0 = applied (or --dry-run); 1 = a request was refused "
            "and nothing was written. Each refusal names the agent and the route "
            "that works."
        ),
    )
    parser.add_argument("--output-dir", required=True, help="The run directory")
    parser.add_argument(
        "--skip", nargs=2, action="append", metavar=("NAME", "REASON"), default=[],
        help="Skip a dispatched agent (→ SKIPPED_OVERRIDE); an already-skipped one is a reported no-op; repeatable",
    )
    parser.add_argument(
        "--dispatch", nargs=2, action="append", metavar=("NAME", "REASON"), default=[],
        help="Dispatch a skipped agent (→ DISPATCH_OVERRIDE); an already-dispatched one is a reported no-op; repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and print; write nothing")
    args = parser.parse_args(argv)
    try:
        result = adjust_dispatch_plan(
            args.output_dir, skips=args.skip, dispatches=args.dispatch, dry_run=args.dry_run,
        )
    except DispatchAdjustmentError as err:
        for problem in err.problems:
            print(f"REJECTED: {problem}")
        return 1
    except (OSError, ValueError) as err:
        print(f"REJECTED: {err}")
        return 1
    prefix = "WOULD " if args.dry_run else ""
    for line in render_adjustments(result["adjustments"]):
        print(prefix + line)
    changed = sum(1 for row in result["adjustments"] if row["changed"])
    print(
        f"{'WOULD ADJUST' if args.dry_run else 'ADJUSTED'}: {changed} | "
        f"DISPATCHING: {result['dispatching']} | SKIPPED: {result['skipped']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

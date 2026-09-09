#!/usr/bin/env python3
"""Record the orchestrator's dispatch-plan adjustments.

The step-5 briefing asks the orchestrator to skip planner-dispatched
reviewers whose focus the diff makes irrelevant, and to force-dispatch a
skipped one it is confident will find something. This module is the
orchestrator's one channel for those adjustments; the plan file is never
edited by hand or by a per-run script.

`--skip NAME REASON` moves a dispatched agent to SKIPPED_OVERRIDE and
`--dispatch NAME REASON` moves a skipped one to DISPATCH_OVERRIDE. Both are
repeatable. Every name is validated against the ``dispatch_plan`` artifact before
anything is written, the read and the single atomic write happen under the
same run-directory lock, `--dry-run` validates and prints without writing,
and one line per adjustment is printed — the same lines the step-6 briefing
repeats. An unknown name, an empty reason, or a malformed or missing plan
exits 1 naming the fix, and writes nothing. An agent already in the requested
family (a `--skip` of a planner-skipped agent, a `--dispatch` of a dispatched
one, a repeat of the same override) is a reported `UNCHANGED` no-op rather
than a refusal, so a re-run after the planner changed its mind is idempotent.
A refused call names the agent, its current status and the transition the
flag allows.

`dispatch_status.OVERRIDE_REASON_KEY`, `PLANNER_STATUS_KEY` and
`ORPHANED_FILES_KEY` are the one spelling of the three fields written here —
the reason, the status the planner gave before the first override, and the
files the skip orphaned — and the orchestration and manifest builders read
them under those names.

Every call recomputes, for each override-skipped agent, the changed files its
scope alone covered. The scope is read from the plan row (`scope_domains`,
the primary plus secondary domains the planner stamps on each row, and a repo
reviewer's `include_paths`) through `plan_dispatch.scope_files`, the same
derivation the planner's own triage uses, so no plan reader has to open the
agent registry. Those files print beside the skip under
`ORPHANED_FILES_LEAD`, the lead the step-6 briefing prints too.

Step 6 lists the overrides from the final plan alone
(`state["dispatch_adjustments"]`) and its briefing repeats them with those
files, so a skip the orchestrator made, and what it left unreviewed, is
visible in the session. Step 9 carries them into
`file_review.override_orphaned_files`, and the coverage section names those
files as skipped by override.

Usage:
    dispatch_adjust.py --output-dir DIR --skip a11y-reviewer "no markup in the diff"
                       --skip security-reviewer "no input, escaping or auth surface"
                       [--dispatch php-tests-reviewer "..."] [--dry-run]
"""

import argparse
import json
import os
import sys

try:
    from . import atomic_io
    from .dispatch_status import (
        DISPATCH_OVERRIDE, DISPATCHED_STATUSES, ORPHANED_FILES_KEY, ORPHANED_FILES_LEAD,
        OVERRIDE_REASON_KEY, PLANNER_STATUS_KEY, SKIPPED_OVERRIDE, SKIPPED_STATUSES,
        load_dispatch_plan,
    )
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
        OVERRIDE_REASON_KEY, PLANNER_STATUS_KEY, SKIPPED_OVERRIDE, SKIPPED_STATUSES,
        load_dispatch_plan,
    )
    from review.plan_dispatch import scope_files
    from review.review_document import normalize_bounded_text
    from review.run_paths import artifact_path

ACTION_SKIP = "skip"
ACTION_DISPATCH = "dispatch"
# What each flag may do: the statuses it applies to and the one it sets.
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


def adjust_dispatch_plan(output_dir, skips=None, dispatches=None, dry_run=False):
    """Apply the adjustments to the run's dispatch plan.

    Returns `{"adjustments": [...], "dispatching": n, "skipped": m,
    "written": bool}`; each adjustment row is `{"name", "from", "to",
    "reason", "changed"}`. An agent already in the requested family — a
    `--skip` of a planner-skipped agent, a `--dispatch` of a dispatched
    one, or a repeat of the same override — is a reported no-op
    (`changed: False`), never a refusal, so a re-run after the planner
    changed its mind is idempotent. Raises DispatchAdjustmentError with
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
            family, target = _TRANSITIONS[action]
            current = agent["status"]
            if current == target:
                rows.append({"name": name, "from": current, "to": target, "reason": reason,
                             "changed": agent.get(OVERRIDE_REASON_KEY) != reason})
            elif current in family:
                rows.append({"name": name, "from": current, "to": target, "reason": reason,
                             "changed": True})
            else:
                # Already skipped (or dispatched) by the planner: nothing to
                # override, and the other flag would flip it the wrong way.
                rows.append({"name": name, "from": current, "to": current, "reason": reason,
                             "changed": False})
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
            "and nothing was written. Each refusal names the agent, its current "
            "status and the flag that applies to it."
        ),
    )
    parser.add_argument("--output-dir", required=True, help="The run directory")
    parser.add_argument(
        "--skip", nargs=2, action="append", metavar=("NAME", "REASON"), default=[],
        help="Skip a dispatched agent (→ SKIPPED_OVERRIDE); a planner-skipped one is a reported no-op; repeatable",
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

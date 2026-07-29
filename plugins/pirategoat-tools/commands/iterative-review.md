---
description: Multi-round independent Codex review on the current branch with pushback tracking and convergence detection. Use when you want an independent external review before creating a PR or merging.
---

You are a senior engineer running a rigorous pre-merge review loop.
An independent reviewer (Codex CLI) examines the current branch's diff,
you triage each finding with engineering judgment, fix real issues, push
back on false positives, then the reviewer checks again — repeating until
the code converges or the round limit is reached.

Your judgment on each finding directly determines code quality.
Rubber-stamping wastes rounds; overcorrecting wastes scope.

Each review invocation has a 30-minute timeout. If the reviewer times out,
the script emits a timeout briefing with instructions — follow them
instead of the three standard outcomes below.

## Setup

**Parse arguments:** `$ARGUMENTS`

Read the user's input as free-form text. Determine:
- **Quick mode**: if the user mentions "quick", "single pass", "one round", or
  similar intent, set `MAX_ROUNDS=1`. Otherwise leave it unset (auto-computed
  from diff size).
- **Max rounds**: if the user specifies a number of rounds (e.g., "3 rounds",
  "--rounds 2", "max 4"), set `MAX_ROUNDS` to that number.
- **Context hints**: any other text is context about what to focus on — use it
  when writing the context file below.

**Construct output directory:**

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_BRANCH=$(git branch --show-current | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
SAFE_REPO_PATH=$(echo "${REPO_ROOT#/}" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
OUTPUT_DIR="/tmp/iterative-review-${SAFE_REPO_PATH}-${SAFE_BRANCH}"
mkdir -p "$OUTPUT_DIR"
```

**Resolve the scripts directory:**

```bash
SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
```

**Ensure the worktree is in a reviewable state:**

Codex only reviews committed code (`merge_base..HEAD`), so uncommitted changes
must be committed first. Do **not** blanket-commit the worktree yourself — it may
hold unrelated edits, secrets, or debris that should not enter history:

```bash
git status --porcelain
```

If there are uncommitted changes, STOP and get the user's decision before
committing anything:

1. Show the user what is uncommitted.
2. Ask whether to commit it (and which changes), or to let them stage/commit or
   stash themselves.
3. Commit only what the user confirms, with semantic commit messages. If the
   user declines, STOP — do not proceed with a dirty tree.

Proceed only once the changes under review are committed. (The per-finding fixes
this loop makes and commits in later rounds are the intended purpose of the
command the user invoked — those do not need per-commit reconfirmation.)

**Compute merge base:**

```bash
BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
if [ -z "$BASE_BRANCH" ]; then
  if git show-ref --verify --quiet refs/remotes/origin/main 2>/dev/null; then
    BASE_BRANCH=main
  elif git show-ref --verify --quiet refs/remotes/origin/trunk 2>/dev/null; then
    BASE_BRANCH=trunk
  elif git show-ref --verify --quiet refs/remotes/origin/develop 2>/dev/null; then
    BASE_BRANCH=develop
  else
    echo 'Cannot detect default branch.' >&2; exit 1
  fi
fi
MERGE_BASE=$(git merge-base "origin/$BASE_BRANCH" HEAD)
```

**Write context file:**

Write a brief summary to `$OUTPUT_DIR/review-context.md` describing:
- What this branch does (goal, scope)
- Key changes and files modified
- What the reviewer should focus on
- Any context hints from `$ARGUMENTS`

Craft this from your session context — you know the codebase and the work done.

## Review Loop

### Round 1

Run the review (from the repo root):

```bash
PYTHONPATH=$SCRIPTS_DIR:$PYTHONPATH python3 -m iterative_review \
  --action review --round 1 \
  --output-dir "$OUTPUT_DIR" \
  --merge-base "$MERGE_BASE" \
  --context-file "$OUTPUT_DIR/review-context.md" \
  [--max-rounds $MAX_ROUNDS]
```

Only include `--max-rounds` if the user specified a round limit or quick mode.

Read the stdout. Four possible outcomes:

**A) UNAVAILABLE message** — Codex CLI is not installed or not authenticated.
Report the specific reason to the user and stop. This is a setup issue, not
a review failure — do not attempt workarounds or apologize.

**B) Completion briefing** — Codex found zero issues. The review is done.
Report the result and stop.

**C) Evaluation briefing** — Codex found issues. Proceed to triage below.

**D) Timeout briefing** — The reviewer timed out. Surface the timeout to the
user with three options: retry this round, skip to next round, or stop
the loop. Wait for the user's decision before proceeding. No round is
recorded until the user acts — retry runs the same round cleanly, skip
proceeds directly to the next round (bypassing advance).

### Triage and Fix

The evaluation briefing lists findings with IDs, severity, file locations,
and descriptions. It also provides structured evaluation steps
(READ → VERIFY → EVALUATE → DECIDE). Follow those steps for each finding.

**RULE 0: Verify before you trust.** Codex is an external reviewer with
limited context. It may misread intent, flag correct code, or miss that a
pattern is deliberate. Read the actual code at the referenced location and
verify the claim before deciding. If you are about to accept a finding
without reading the code, STOP — that is rubber-stamping.

For rounds 2+, reference your prior round outcomes. Avoid re-introducing
patterns you already fixed. If the reviewer re-flags something you rejected
in a prior round, check whether new evidence exists before changing your
decision.

- **Read the code** at the referenced location
- **If real** — fix it, following this sequence:
  1. **Check for siblings**: The reviewer sees the diff, not the full codebase
     — it may flag one instance of a pattern that recurs elsewhere in this
     branch's changes. Search files this branch touches for the same pattern
     and fix all instances together. Stay within the branch's scope — a finding
     about one endpoint does not justify sweeping every other endpoint. Note
     important out-of-scope siblings as follow-ups.
  2. **Right-size the fix** based on where the root cause lives:
     - Pre-existing code (before this branch): minimal, targeted fix
     - Our branch's code: question the approach and refactor if it's a design
       symptom. Patching symptoms burns rounds; fixing root causes converges
       faster.
     - Multiple findings → same design problem: reconsider the structure
  3. **Commit** with a semantic commit message. One logical change per commit.
- **If wrong** — push back: cite the specific code that contradicts the finding
- **If valid but out of scope** — defer: state what makes it out of scope for
  this branch
- **If uncertain** — investigate deeper before deciding: read more context,
  check git history

### Write Outcomes

Write `$OUTPUT_DIR/round-N-outcomes.json` — an array where each entry has:

```json
[
  {"id": "r1_f1", "severity": "P1", "action": "fixed", "summary": "Added null check."},
  {"id": "r1_f2", "severity": "P0", "action": "rejected", "reasoning": "Input is pre-validated at caller."},
  {"id": "r1_f3", "severity": "P3", "action": "deferred", "reasoning": "Valid but out of scope for this PR."}
]
```

Every finding ID must have an outcome. Copy severity from the finding. No extra IDs.

### Advance

```bash
PYTHONPATH=$SCRIPTS_DIR:$PYTHONPATH python3 -m iterative_review \
  --action advance --round N \
  --output-dir "$OUTPUT_DIR"
```

Read the stdout. Two outcomes:

**A) "Proceed to review round M"** — run the next review:

```bash
PYTHONPATH=$SCRIPTS_DIR:$PYTHONPATH python3 -m iterative_review \
  --action review --round M \
  --output-dir "$OUTPUT_DIR"
```

Then repeat from Triage above.

**B) "Review Loop Complete"** — the loop converged. Proceed to Completion.

## Completion

Report the final result:
- Termination reason (zero findings, all addressed, max rounds, hard limit)
- Rounds completed and stats (fixed, rejected, deferred)
- If `$OUTPUT_DIR/review-loop-result.json` has `deferred_items`, list them —
  these should go into the PR description under a `## Follow-ups` section.
  Cross-check: if a deferred item describes the same issue as something you
  fixed in a later round (even with different wording), drop it.

The review artifacts are in `$OUTPUT_DIR/` for reference.

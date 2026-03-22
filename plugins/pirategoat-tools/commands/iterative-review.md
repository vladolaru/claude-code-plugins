---
description: Multi-round independent Codex review on the current branch with pushback tracking and convergence detection. Use when you want an independent external review before creating a PR or merging.
---

You are orchestrating an iterative Codex review loop on the current branch.
Codex CLI runs an independent review, you triage and fix findings, then
Codex reviews again — repeating until the code converges (zero findings,
all addressed, or max rounds reached).

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

The `iterative_review` module lives inside the pirategoat-tools plugin. Resolve
the path so you can set PYTHONPATH:

```bash
SCRIPTS_DIR="<pirategoat-tools-plugin-root>/scripts"
```

Use the plugin root from your environment (the directory containing this command
file, two levels up: `commands/` → plugin root, then into `scripts/`).

**Ensure all changes are committed:**

Run `git status`. If there are uncommitted changes, commit them with semantic
commit messages before proceeding. The review script blocks on uncommitted
changes — Codex only reviews committed code (merge_base..HEAD).

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

Read the stdout. Two possible outcomes:

**A) Completion briefing** — Codex found zero issues. The review is done.
Report the result and stop.

**B) Evaluation briefing** — Codex found issues. Proceed to triage below.

### Triage and Fix

The evaluation briefing lists findings with IDs, severity, file locations,
and descriptions. For each finding:

Codex is an external reviewer — be skeptical. It may lack context, misread
intent, or flag code that's correct for reasons it can't see. Verify each
claim against the actual code before deciding.

- **Read the code** at the referenced location
- **If real**: fix it. Right-size the fix based on where the root cause lives:
  - Pre-existing code (before this branch): minimal, targeted fix
  - Our branch's code: if it's a symptom of a design decision we made, question
    the approach and refactor. That's not scope creep — it's fixing our work.
    Patching symptoms burns rounds; fixing root causes converges faster.
  - If multiple findings point to the same design problem, reconsider the structure
- **If wrong**: push back — note the technical reason, reference the code
- **If valid but out of scope**: defer with reasoning
- **If you can't tell**: investigate further before deciding

After fixing, commit each fix with a semantic commit message. One logical
change per commit.

### Write Outcomes

Write `$OUTPUT_DIR/round-N-outcomes.json` — an array where each entry has:

```json
[
  {"id": "r1_f1", "action": "fixed", "summary": "Added null check."},
  {"id": "r1_f2", "action": "rejected", "reasoning": "Input is pre-validated at caller."},
  {"id": "r1_f3", "action": "deferred", "reasoning": "Valid but out of scope for this PR."}
]
```

Every finding ID must have an outcome. No extra IDs.

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

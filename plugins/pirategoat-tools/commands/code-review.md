---
description: Incremental code review of new commits on the current branch since the last review
---

You are a code review orchestrator. Your mission: ensure the review pipeline
runs to completion with dedication, precision, and care — producing a
comprehensive, accurate, and actionable review of the code changes that the
author can act on and that maintains a high quality bar for the codebase and
its users.

This run reviews **new commits since the last review** on this branch. On
first run, reviews all changes (same as `/full-code-review`).

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract — write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Empty: incremental review (only new commits since last review)
- `full` or `reset`: delete the review baseline and do a full review
- Branch name: review that branch incrementally
- Explicit git range (contains `..`): review that range

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_BRANCH=$(git branch --show-current | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
SAFE_REPO_PATH=$(echo "${REPO_ROOT#/}" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
OUTPUT_DIR="/tmp/branch-review-${SAFE_REPO_PATH}-${SAFE_BRANCH}"
mkdir -p "$OUTPUT_DIR"

# Default review mode; full/reset switches it to a clean full review below.
MODE=incremental
```

**Handle full/reset mode:**

If `$ARGUMENTS` is `full` or `reset`, delete the baseline and switch to full mode:
```bash
rm -f "${OUTPUT_DIR}/.branch-review-baseline.json"
MODE=full
```

**Run Step 1:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --step 1 --mode "$MODE" --output-dir "$OUTPUT_DIR" \
  --session-id "${CLAUDE_SESSION_ID}"
```

If an explicit git range was provided, add `--git-range "<RANGE>"`.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

---
description: Run a full multi-agent code review on the current branch's changes (no PR required)
---

You are a code review orchestrator. Your mission: dispatch specialized
reviewer agents against the current branch's changes and synthesize
their findings into a unified review.

This is a **branch-level review** — no PR or GitHub context required.
Useful for pre-PR feedback during development.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
execute the briefing, then call it again for the next step indicated in
the output. The script handles all mode-specific logic internally.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Empty: auto-detect default branch and review full range
- Branch name: use that branch
- Explicit git range (contains `..`): use that range

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_BRANCH=$(git branch --show-current | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
SAFE_REPO_PATH=$(echo "$REPO_ROOT" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/branch-review-${SAFE_REPO_PATH}-${SAFE_BRANCH}"
mkdir -p "$OUTPUT_DIR"
```

**Run Step 1:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-pipeline.py \
  --step 1 --mode full --output-dir "$OUTPUT_DIR"
```

If an explicit git range was provided, add `--git-range "<RANGE>"`.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

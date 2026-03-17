---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: run the full PR review
pipeline autonomously and produce a saved review document with validated
findings.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
execute the briefing, then call it again for the next step indicated in
the output. The script handles all mode-specific logic internally.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Required: PR URL or PR number
- If empty: STOP. "Usage: `/pr-review <PR_URL_or_number>`"
- Extract PR number from URL if needed (`.../pull/3817` → `3817`)

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_REPO_PATH=$(echo "$REPO_ROOT" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/pr-review-${SAFE_REPO_PATH}-<PR_NUMBER>"
mkdir -p "$OUTPUT_DIR"
```

**Run Step 1:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-pipeline.py \
  --step 1 --mode pr --output-dir "$OUTPUT_DIR" --pr-number "<PR_NUMBER>"
```

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

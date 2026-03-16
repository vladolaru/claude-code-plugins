---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: run the full PR review
pipeline autonomously and produce a saved review document with validated
findings.

## Workflow

A Python script provides step-specific instructions. You call it once per
step, execute the instructions it prints, then call it again for the next
step. Mode switching (bot vs default) is handled by the script — follow
whatever instructions it provides.

## Phase Overview

| Phase | Steps | What happens |
|-------|-------|-------------|
| SETUP | 0-2 | Parse PR number, repo setup (skipped in bot mode), context discovery |
| AWARENESS | 3 | Review context summary (reviews, linked issues, PR metadata) |
| CONTEXT | 4-5 | Fetch issue context, summarize |
| EXECUTION | 6-10 | Size assessment, ground truth, dispatch plan + triage, parallel agents, reconcile + verify |
| REVIEW | 11 | Generate review report |
| VALIDATION | 12 | Decision critic |
| OUTPUT | 13-14 | Present results, cleanup |

## Failure Recovery

| Failure point | Recovery |
|---------------|----------|
| Before PR details (Steps 0-1) | STOP. Restore branch/stash if touched. |
| After PR details, before dispatch (Steps 2-6) | Write partial report, skip to Step 11. |
| During dispatch (Step 9) | Continue with available agents. Note failures. |
| Reconciliation failure (Step 10) | Note "No findings" and proceed to Step 11. |
| Decision critic error (Step 12) | Skip critic, present as-is. |

## Invocation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pr-review-pipeline.py \
  --step-number <0-14> \
  --total-steps 14 \
  --pr-number "<PR number>" \
  --output-dir "/tmp/pr-review-<SAFE_REPO_PATH>-<PR_NUMBER>" \
  --thoughts "<accumulated state from all previous steps>"
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--step-number` | Yes | Current step (0-14) |
| `--total-steps` | Yes | Always 14 |
| `--pr-number` | Step 0 | PR number. Steps 1-14 read from `--thoughts`. |
| `--output-dir` | Step 1 | Output directory. Bot mode detected from `review-context.json` here. |
| `--thoughts` | Yes | All accumulated state. Pass `""` on step 0. |

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

**Run Step 0:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pr-review-pipeline.py \
  --step-number 0 \
  --total-steps 14 \
  --pr-number "<PR_NUMBER>" \
  --output-dir "$OUTPUT_DIR" \
  --thoughts ""
```

Execute the instructions printed by the script. After completing each
step, call the script with `--step-number N+1` and pass ALL accumulated
state in `--thoughts`. Continue until Step 14 completes.

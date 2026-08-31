---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a code review orchestrator. Your mission: ensure the review pipeline
runs to completion with dedication, precision, and care — producing a
comprehensive, accurate, and actionable review of the code changes that the
author can act on and that maintains a high quality bar for the codebase and
its users.

This run reviews a **pull request** — PR metadata, review history, and linked
issues provide additional context for the review.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract — write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Required: PR URL or PR number
- If empty: STOP. "Usage: `/pr-review <PR_URL_or_number>`"
- Extract PR number from URL if needed (`.../pull/3817` → `3817`)

**Detect quick review mode:** If the user's input clearly indicates they want
a quick or fast review (e.g., "quick", "fast", "quick mode", "light review"),
add `--quick` to the first `pipeline.py` call. Examples:
- `/pr-review 42 quick` → add `--quick`
- `/pr-review quick mode https://github.com/.../pull/42` → add `--quick`
- `/pr-review 42` → do NOT add `--quick` (standard review)

**Detect dependency refresh mode:** If the user's input clearly asks to refresh or install dependencies before the review (e.g., "refresh deps", "refresh dependencies", "update dependencies first", "install deps first"), add `--refresh-deps` to the first `pipeline.py` call. This is trusted-branch mode: after a clean-tracked-worktree safety check, the interactive orchestrator inspects the trusted worktree and refreshes dependencies adaptively — only add the flag when the user asked. Examples:
- `/pr-review 42 refresh deps` → add `--refresh-deps`
- `/pr-review 42 with fresh dependencies` → add `--refresh-deps`
- `/pr-review 42` → omit the flag

An omitted flag falls back to the requester's machine-local default
(`~/.config/pirategoat/config.json` with `review.refresh_dependencies: true`
turns refresh on for every interactive run). If the user asks to skip the
refresh for this run, add `--no-refresh-deps`.

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
OUTPUT_DIR=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/run_paths.py allocate --kind pr --repo-root "$REPO_ROOT" --target "<PR_NUMBER>")
```

**Run Step 1:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --step 1 --mode pr --output-dir "$OUTPUT_DIR" --pr-number "<PR_NUMBER>" \
  --session-id "${CLAUDE_SESSION_ID}" [--quick] [--refresh-deps]
```

Add `--quick` only if the user indicated they want a quick review; add
`--refresh-deps` only if they asked to refresh dependencies.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

---
description: Run a full multi-agent code review on the current branch's changes (no PR required)
---

You are a code review orchestrator. Your mission: ensure the review pipeline
runs to completion with dedication, precision, and care — producing a
comprehensive, accurate, and actionable review of the code changes that the
author can act on and that maintains a high quality bar for the codebase and
its users.

This run reviews **all changes on the current branch** — no PR or GitHub
context required. Useful for pre-PR feedback during development.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract — write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Empty: auto-detect default branch and review full range
- Branch name: use that branch
- Explicit git range (contains `..`): use that range

**Detect dependency refresh mode:** If the user's input clearly asks to refresh or install dependencies before the review (e.g., "refresh deps", "refresh dependencies", "update dependencies first", "install deps first"), add `--refresh-deps` to the first `pipeline.py` call. This is trusted-branch mode: after a clean-tracked-worktree safety check, the interactive orchestrator inspects the trusted worktree and refreshes dependencies adaptively — only add the flag when the user asked. Examples:
- `/full-code-review refresh deps` → add `--refresh-deps`
- `/full-code-review` → omit the flag

An omitted flag falls back to the requester's machine-local default
(`~/.config/pirategoat/config.json` with `review.refresh_dependencies: true`
turns refresh on for every interactive run). If the user asks to skip the
refresh for this run, add `--no-refresh-deps`.

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
OUTPUT_DIR=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/run_paths.py allocate --kind branch --repo-root "$REPO_ROOT" --target "$(git branch --show-current)")
```

**Run Step 1:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --step 1 --mode full --output-dir "$OUTPUT_DIR" \
  --session-id "${CLAUDE_SESSION_ID}" [--refresh-deps]
```

If an explicit git range was provided, add `--git-range "<RANGE>"`. Add
`--refresh-deps` only if the user asked to refresh dependencies.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

---
name: code-review
description: "Incremental code review of new commits on the current branch since the last review"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/code-review.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


You are a code review orchestrator. Your mission: ensure the review pipeline
runs to completion with dedication, precision, and care - producing a
comprehensive, accurate, and actionable review of the code changes that the
author can act on and that maintains a high quality bar for the codebase and
its users.

This run reviews **new commits since the last review** on this branch. On
first run, reviews all changes (same as `$pirategoat-tools:full-code-review`).

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract - write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `${CODEX_SKILL_ARGUMENTS}`
- Empty: incremental review (only new commits since last review)
- `full` or `reset`: delete the review baseline and do a full review
- Branch name: review that branch incrementally
- Explicit git range (contains `..`): review that range

**Detect dependency refresh mode:** If the user's input clearly asks to refresh or install dependencies before the review (e.g., "refresh deps", "refresh dependencies", "update dependencies first", "install deps first"), add `--refresh-deps` to the first `pipeline.py` call. This is trusted-branch mode: after a clean-tracked-worktree safety check, the interactive orchestrator inspects the trusted worktree and refreshes dependencies adaptively - only add the flag when the user asked. Examples:
- `$pirategoat-tools:code-review refresh deps` → add `--refresh-deps`
- `$pirategoat-tools:code-review` → omit the flag

An omitted flag falls back to the requester's machine-local default
(`~/.config/pirategoat/config.json` with `review.refresh_dependencies: true`
turns refresh on for every interactive run). If the user asks to skip the
refresh for this run, add `--no-refresh-deps`.

**Construct output directory** (sanitize all fragments):

```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
REPO_ROOT=$(git rev-parse --show-toplevel)
OUTPUT_DIR=$(python3 ${CODEX_PLUGIN_ROOT}/scripts/review/run_paths.py allocate --kind branch --repo-root "$REPO_ROOT" --target "$(git branch --show-current)")

# Default review mode; full/reset switches it to a clean full review below.
MODE=incremental
```

**Handle full/reset mode:**

If `${CODEX_SKILL_ARGUMENTS}` is `full` or `reset`, delete the baseline and switch to full mode:
```bash
TARGET_DIR=$(dirname "$(dirname "$OUTPUT_DIR")")
rm -f "${TARGET_DIR}/.branch-review-baseline.json"
MODE=full
```

**Run Step 1:**

```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
python3 ${CODEX_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --host codex \
  --step 1 --mode "$MODE" --output-dir "$OUTPUT_DIR" \
  --session-id "${CODEX_THREAD_ID}" [--refresh-deps]
```

If an explicit git range was provided, add `--git-range "<RANGE>"`. Add
`--refresh-deps` only if the user asked to refresh dependencies.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

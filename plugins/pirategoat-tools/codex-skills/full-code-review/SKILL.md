---
name: full-code-review
description: "Run a full multi-agent code review on the current branch's changes (no PR required)"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/full-code-review.md -->

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

This run reviews **all changes on the current branch** - no PR or GitHub
context required. Useful for pre-PR feedback during development.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract - write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `${CODEX_SKILL_ARGUMENTS}`
- Empty: auto-detect default branch and review full range
- Branch name: use that branch
- Explicit git range (contains `..`): use that range

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_BRANCH=$(git branch --show-current | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
SAFE_REPO_PATH=$(echo "${REPO_ROOT#/}" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
OUTPUT_DIR="/tmp/branch-review-${SAFE_REPO_PATH}-${SAFE_BRANCH}"
mkdir -p "$OUTPUT_DIR"
```

**Run Step 1:**

```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
python3 ${CODEX_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --host codex \
  --step 1 --mode full --output-dir "$OUTPUT_DIR"
```

If an explicit git range was provided, add `--git-range "<RANGE>"`.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

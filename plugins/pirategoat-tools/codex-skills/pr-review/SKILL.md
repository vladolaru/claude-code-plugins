---
name: pr-review
description: "End-to-end PR review - gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/pr-review.md -->

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

This run reviews a **pull request** - PR metadata, review history, and linked
issues provide additional context for the review.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract - write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `${CODEX_SKILL_ARGUMENTS}`
- Required: PR URL or PR number
- If empty: STOP. "Usage: `$pirategoat-tools:pr-review <PR_URL_or_number>`"
- Extract PR number from URL if needed (`.../pull/3817` → `3817`)

**Detect quick review mode:** If the user's input clearly indicates they want
a quick or fast review (e.g., "quick", "fast", "quick mode", "light review"),
add `--quick` to the first `pipeline.py` call. Examples:
- `$pirategoat-tools:pr-review 42 quick` → add `--quick`
- `$pirategoat-tools:pr-review quick mode https://github.com/.../pull/42` → add `--quick`
- `$pirategoat-tools:pr-review 42` → do NOT add `--quick` (standard review)

**Construct output directory** (sanitize all fragments):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_REPO_PATH=$(echo "${REPO_ROOT#/}" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-')
OUTPUT_DIR="/tmp/pr-review-${SAFE_REPO_PATH}-<PR_NUMBER>"
mkdir -p "$OUTPUT_DIR"
```

**Run Step 1:**

```bash
python3 ${CODEX_PLUGIN_ROOT}/scripts/review/pipeline.py \
  --host codex \
  --step 1 --mode pr --output-dir "$OUTPUT_DIR" --pr-number "<PR_NUMBER>" [--quick]
```

Add `--quick` only if the user indicated they want a quick review.

Execute the briefing printed by the script. Then call with `--step N`
where N is the next step indicated in the output. Continue until the
script signals PIPELINE COMPLETE.

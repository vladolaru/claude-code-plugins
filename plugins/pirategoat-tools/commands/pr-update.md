---
description: Analyze the current PR branch and update the PR description with an accurate, validated summary proportional to PR size
---

You are a PR description updater. Your mission: analyze the branch, discover relevant artifacts (plans, reviews), respect the project's PR template, generate an accurate description proportional to PR size, validate every claim against the actual diff, and update the PR after user approval.

**Key principle:** Project PR templates take priority. ALWAYS detect and use the project's template structure. The default template is only a fallback.

## Step 1: Detect PR and Validate State

**Parse arguments:** `$ARGUMENTS`
- If empty: auto-detect PR from current branch
- If a PR number or URL: use directly

**Detection:**

```bash
# Try gh first, then ghe for GitHub Enterprise
gh pr view --json number,title,body,baseRefName,headRefName,state,isDraft 2>/dev/null || \
ghe pr view --json number,title,body,baseRefName,headRefName,state,isDraft 2>/dev/null
```

Store which CLI worked as `GH_CMD` (`gh` or `ghe`) for all subsequent calls.

**STOP conditions — halt with a clear message if any apply:**
- No PR found for the current branch → "No PR found. Create one first with `gh pr create`."
- PR state is `MERGED` or `CLOSED` → "PR is already merged/closed."
- Current branch is the default branch and no PR number was given → "You're on the default branch. Specify a PR number."
- Neither `gh` nor `ghe` is available → "GitHub CLI not found. Install `gh` or `ghe`."

**Store:** `PR_NUMBER`, `PR_TITLE`, `CURRENT_BODY`, `BASE_REF`, `HEAD_REF`, `IS_DRAFT`, `GH_CMD`.

## Step 2: Gather Branch Context

Collect raw material in one pass:

```bash
# Commit history
git log --oneline ${BASE_REF}..HEAD

# Change summary (files and line counts)
git diff --stat ${BASE_REF}..HEAD

# Files added/modified/deleted
git diff --name-status ${BASE_REF}..HEAD
```

**Determine PR size category** from file count and total line delta:

| Category | Criteria | Description Depth |
|----------|----------|-------------------|
| **Small** | 1–3 files, < 100 lines | Concise — 2–3 sentences per section |
| **Medium** | 4–15 files, 100–500 lines | Full sections with key decisions |
| **Large** | 15+ files, 500+ lines | Thorough — architecture, trade-offs, deferral notes |

## Step 3: Detect PR Template

**ALWAYS check for a project-level template first.** Search standard locations in priority order:

```bash
# Priority 1: .github directory (most common)
cat .github/PULL_REQUEST_TEMPLATE.md 2>/dev/null || \
cat .github/pull_request_template.md 2>/dev/null || \
# Priority 2: .github/PULL_REQUEST_TEMPLATE/ directory (use first file found).
# Guard with a conditional so an empty directory does not run `cat` with no
# argument (which would read stdin and stall / falsely short-circuit the chain).
{ tmpl=$(ls .github/PULL_REQUEST_TEMPLATE/*.md 2>/dev/null | head -1); [ -n "$tmpl" ] && cat "$tmpl"; } || \
# Priority 3: repo root
cat PULL_REQUEST_TEMPLATE.md 2>/dev/null || \
# Priority 4: docs/
cat docs/PULL_REQUEST_TEMPLATE.md 2>/dev/null
```

**If a template is found:**
- Use its section structure exactly — no extra sections, no missing sections
- Fill every section meaningfully
- Complete any checklists (check items that apply, leave unchecked items that don't)
- Map analysis to template sections (e.g., "What?" = summary, "How?" = implementation details, "Testing Instructions" = smoketest steps)
- Note which template file was used for Step 7

**If NO template found**, use this default structure:

```markdown
## Summary
## Key Changes
## Current State
## Testing Steps
## Deferred Work
```

Skip "Deferred Work" if nothing was deferred.

## Step 4: Discover and Filter Artifacts

**Skip this step for small PRs** — the diff tells the story.

For medium and large PRs, resolve the most recent matching review artifact
before looking — otherwise the artifacts are silently missed:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

| Location | What | Relevance Check |
|----------|------|-----------------|
| `$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/run_paths.py latest --kind branch --repo-root "$REPO_ROOT" --target "$(git branch --show-current)")/review-findings.md` | `/full-code-review` or `/code-review` findings | Same repo + branch? |
| `$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/run_paths.py latest --kind pr --repo-root "$REPO_ROOT" --target "${PR_NUMBER}")/review-findings.md` | `/pr-review` findings | Same repo + PR number? |
| `~/.claude/plans/*.md` | Implementation plans | References this branch's files? |
| `.claude/docs/plans/*.md` | Project-level plans | Date and content match? |
| `.claude/reviews/*.md` | Code review output | References this branch? |

**For each candidate artifact:**
1. Read it and check if it references the current branch, PR number, or changed files
2. Cross-reference planned items with the actual diff — were they implemented?
3. Check if review findings were addressed in later commits
4. **Discard artifacts about different work** — don't guess, verify

Store: `RELEVANT_ARTIFACTS` (paths + brief summary of what's relevant from each). If none found, note "no artifacts consulted."

## Step 5: Analyze and Draft Description

Read the actual changed files (use the diff, not full files). Understand:

- **What** was done — from diff + commit messages
- **Why** — from commit messages, plan artifacts, code comments
- **Key decisions** and trade-offs made
- **What was tested** — test files in the diff, CI status
- **What was deferred** — commit messages with "TODO", "follow-up", "later"; plan items not yet implemented

### Scope Rule

Only describe what this PR changes. Do NOT include:
- Observations about pre-existing code
- Improvements noticed during review
- Suggestions for other files

If a review artifact mentions issues outside the diff, those are out of scope for the description.

### Template Mapping

If a project template was found, map each finding to the appropriate template section. Do not restructure the template.

### Brevity Calibration

Match depth to the size category determined in Step 2:
- **Small PRs:** Skip sections that add no value. 2–3 sentences per section.
- **Medium PRs:** Full sections with key decisions explained.
- **Large PRs:** Include explicit subsections, architectural rationale, and deferral notes.

### Artifact Integration

Synthesize artifact insights into the description — do NOT paste artifact content verbatim. Reference plans/reviews as context, note what was addressed.

## Step 6: Validation Pass

**CRITICAL** — before showing the user, verify every factual claim. The description must be strictly scoped to what this PR actually changes.

### 6a. Scope Check (Most Important)

For every statement in the description, ask: "Is this about code that appears in `git diff ${BASE_REF}..HEAD`?"

If not, remove it. The PR description describes THIS PR's changes, nothing else.

### 6b. File/Path References

Each mentioned file must exist in `git diff --name-only ${BASE_REF}..HEAD`. If a file is referenced for context (not changed by this PR), clearly label it as context, not as a change.

### 6c. Testing Steps

- Referenced commands exist (check `package.json` scripts, Makefile targets, etc.)
- Referenced URLs/paths are plausible
- Steps test the PR's changes, not unrelated functionality

### 6d. Code Claims

Each "added X", "removed Y", "changed Z" must match the actual diff hunk. Describe what THIS PR changes about the code, not what the code does in general.

### 6e. Artifact Claims

Review findings and plan items referenced in the description were actually addressed by commits in this PR. Findings about code outside the diff are out of scope.

### 6f. Correct or Remove

Remove or correct anything that fails verification. Flag uncertainty explicitly rather than assert false confidence.

## Step 7: Present and Get Approval

Show the complete description with metadata:

```
PR: #<PR_NUMBER> — <PR_TITLE>
Size: <category> (<N> files, ~<M> lines)
Template: <project template path> | default
Artifacts: <paths consulted> | none

--- PROPOSED DESCRIPTION ---

<full generated description>

--- END ---
```

**Wait for explicit user approval.** Do NOT update the PR until the user confirms.

If the user requests changes: edit the description, re-run validation (Step 6), and re-present.

## Step 8: Update PR

After approval:

```bash
# Write description to temp file for safe --body-file usage
cat > ${TMPDIR:-/tmp}/pr-description-${PR_NUMBER}.md << 'PRBODY'
<generated description>
PRBODY

# Update the PR
${GH_CMD} pr edit ${PR_NUMBER} --body-file ${TMPDIR:-/tmp}/pr-description-${PR_NUMBER}.md
```

Verify the update succeeded:

```bash
${GH_CMD} pr view ${PR_NUMBER} --json url -q .url
```

Report success with the PR URL. Clean up the temp file:

```bash
rm -f ${TMPDIR:-/tmp}/pr-description-${PR_NUMBER}.md
```

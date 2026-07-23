---
name: switch-to
description: "Switch to a branch or PR - handles dirty state, remote sync, fork remotes, and post-switch context"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/switch-to.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


You are a branch switcher. Your mission: safely switch the current repo to a target branch (by name or PR URL), handling dirty working trees, remote synchronization, and fork remotes along the way.

**RULE 0: Preserve uncommitted work.** Check for dirty state and get user consent before any branch switch.

**RULE 1: Always show meaningful post-switch context.** The user should know where they landed.

**Expected failures:** Git and GitHub CLI commands may fail for normal reasons (network issues, auth prompts, branch not found). When a command fails, report the error clearly to the user and STOP with actionable guidance. Do not apologize or retry blindly.

## Step 1: Parse Arguments

**Parse arguments:** `${CODEX_SKILL_ARGUMENTS}`
- If empty: STOP. Tell the user: "Usage: `$pirategoat-tools:switch-to <branch_name_or_PR_URL>`"
- If argument contains `/pull/` (a GitHub PR URL): go to **Step 2A (PR flow)**
- Otherwise: treat as a branch name, go to **Step 2B (Branch flow)**

Store `CURRENT_BRANCH`:
```bash
git branch --show-current
```

**Early exit:** For the branch flow (not PR), if `CURRENT_BRANCH` equals `TARGET_BRANCH`, tell the user "Already on `<branch>`" and skip to **Step 5** (remote sync check). For PR flow, always proceed - the user wants PR context even if already on the branch.

## Step 2A: PR Flow - Gather PR Details

```bash
gh pr view <PR_URL> --json headRefName,baseRefName,headRepositoryOwner,url,title,state,author,number,headRepository
```

Store:
- `PR_NUMBER` = number
- `PR_TITLE` = title
- `PR_STATE` = state
- `PR_AUTHOR` = author.login
- `HEAD_BRANCH` = headRefName
- `BASE_BRANCH` = baseRefName
- `HEAD_OWNER` = headRepositoryOwner.login
- `HEAD_REPO` = headRepository.name

**Validate CWD repo matches the PR's repo:**

```bash
gh repo view --json owner,name
```

Compare the CWD repo's `owner.login/name` against the PR's base repository. If they don't match:

STOP. Tell the user: "This PR belongs to `<pr_owner>/<pr_repo>` but you're in `<cwd_owner>/<cwd_repo>`. Navigate to the correct repo first."

**Check if PR is from a fork** - compare `HEAD_OWNER` against the CWD repo owner. If different, this is a fork PR:

```bash
# Check if fork remote already exists
git remote get-url <HEAD_OWNER> 2>/dev/null

# If the remote doesn't exist, add it
git remote add <HEAD_OWNER> https://github.com/<HEAD_OWNER>/<HEAD_REPO>.git
```

Set `REMOTE_NAME` = `<HEAD_OWNER>` for fork PRs, or `origin` for same-repo PRs.

Set `TARGET_BRANCH` = `HEAD_BRANCH` and `IS_PR = true`.

Proceed to **Step 3**.

## Step 2B: Branch Flow

Set `TARGET_BRANCH` = the branch name argument.
Set `REMOTE_NAME` = `origin`.
Set `IS_PR = false`.

Proceed to **Step 3**.

## Step 3: Handle Dirty Working Tree

Check for uncommitted changes:
```bash
git status --porcelain
```

**If output is empty:** working tree is clean. Proceed to **Step 4**.

**If output is non-empty:** summarize what's dirty (N modified, M untracked, K staged) and ask the user:

```
the host's user-input mechanism:
  question: "You have uncommitted changes on `<CURRENT_BRANCH>`. How would you like to proceed?"
  header: "Uncommitted changes detected"
  options:
    - label: "Stash changes"
      description: "Run git stash push and proceed with the switch"
    - label: "Commit first"
      description: "Stop here so you can commit your changes before switching"
    - label: "Cancel"
      description: "Abort the switch entirely"
```

Handle the user's choice:

- **Stash changes:**
  ```bash
  git stash push -m "switch-to: stashed from <CURRENT_BRANCH> before switching to <TARGET_BRANCH>"
  ```
  Store `STASHED = true`. Proceed to **Step 4**.

- **Commit first:** STOP. Tell the user: "Commit your changes on `<CURRENT_BRANCH>`, then re-run `$pirategoat-tools:switch-to <original_argument>`."

- **Cancel:** STOP.

## Step 4: Switch to Target Branch

**Check if the branch exists locally:**
```bash
git branch --list <TARGET_BRANCH>
```

**Case A - Branch exists locally:**
```bash
git checkout <TARGET_BRANCH>
```
Proceed to **Step 5**.

**Case B - Branch does NOT exist locally:**

Check if it exists on the remote:
```bash
git ls-remote --heads <REMOTE_NAME> <TARGET_BRANCH>
```

If it exists on the remote - create a local tracking branch:
```bash
git fetch <REMOTE_NAME> <TARGET_BRANCH>
git checkout -b <TARGET_BRANCH> <REMOTE_NAME>/<TARGET_BRANCH>
```
Proceed to **Step 6** (skip Step 5 - the branch was just fetched, it's up to date).

If it does NOT exist on the remote either:

**Safety: restore stashed changes before stopping.** If `STASHED` is true, run `git stash pop` to return the user's work to their working tree.

STOP. Tell the user: "Branch `<TARGET_BRANCH>` not found locally or on `<REMOTE_NAME>`. Your working tree has been restored."

## Step 5: Sync with Remote

This step only runs when the branch already existed locally (Case A in Step 4).

Check if the remote has new commits:
```bash
git fetch <REMOTE_NAME> <TARGET_BRANCH>
git log HEAD..<REMOTE_NAME>/<TARGET_BRANCH> --oneline
```

**If there are NO new remote commits:** proceed to **Step 6**.

**If there ARE new remote commits:** show them and ask:

```
the host's user-input mechanism:
  question: "There are N new commit(s) on `<REMOTE_NAME>/<TARGET_BRANCH>` not in your local branch:\n\n<commit list>\n\nPull them?"
  header: "Remote has new commits"
  options:
    - label: "Pull (rebase)"
      description: "git pull --rebase <REMOTE_NAME> <TARGET_BRANCH>"
    - label: "Pull (merge)"
      description: "git pull <REMOTE_NAME> <TARGET_BRANCH>"
    - label: "Skip"
      description: "Stay on local version without pulling remote changes"
```

Handle the user's choice:
- **Pull (rebase):** `git pull --rebase <REMOTE_NAME> <TARGET_BRANCH>`
- **Pull (merge):** `git pull <REMOTE_NAME> <TARGET_BRANCH>`
- **Skip:** do nothing, proceed.

## Step 6: PR-Specific Post-Switch Tasks

**Skip this step if `IS_PR` is false.**

Fetch the target base branch so the user has it locally for comparisons:
```bash
git fetch origin <BASE_BRANCH>
```

## Step 7: Post-Switch Context

Show the user a concise summary of where they landed.

**Always show:**
```bash
# Recent commits on this branch
git log --oneline -10

# Ahead/behind vs remote tracking branch
# Output format: BEHIND<tab>AHEAD
#   Column 1 (left)  = commits in REMOTE not in HEAD → BEHIND count
#   Column 2 (right) = commits in HEAD not in REMOTE → AHEAD count
git rev-list --left-right --count <REMOTE_NAME>/<TARGET_BRANCH>...HEAD 2>/dev/null
```

**If `IS_PR` is true, also show:**
```bash
# Ahead/behind vs base branch
# SAME column order: Column 1 = BEHIND, Column 2 = AHEAD
git rev-list --left-right --count origin/<BASE_BRANCH>...HEAD

# PR metadata
gh pr view <PR_NUMBER> --json title,state,author,labels,reviewDecision,statusCheckRollup
```

**CRITICAL - interpreting `git rev-list --left-right --count A...B`:**
The output is two tab-separated numbers. For `A...HEAD`:
- **Column 1 (left/A side)** = commits in A not in HEAD = how far HEAD is **behind** A
- **Column 2 (right/HEAD side)** = commits in HEAD not in A = how far HEAD is **ahead** of A

Example: output `13	6` for `origin/trunk...HEAD` means **6 ahead, 13 behind** (NOT 13 ahead, 6 behind).

Present a concise summary. Always include:

```
Switched to `<TARGET_BRANCH>`

Recent commits:
  <last 5-10 commits, one per line>
```

**If `IS_PR` is true**, append:

```
PR #<PR_NUMBER>: <PR_TITLE>
  Author: <PR_AUTHOR>  |  State: <PR_STATE>  |  Review: <reviewDecision or "pending">
  Base: <BASE_BRANCH> - <AHEAD> ahead, <BEHIND> behind
  Checks: <summary of statusCheckRollup - e.g. "3/4 passed, 1 pending">
```

Where `AHEAD` = column 2 and `BEHIND` = column 1 from the `rev-list` output.

**If `STASHED` is true**, append: "Your changes from `<CURRENT_BRANCH>` are stashed. Run `git stash pop` after switching back."

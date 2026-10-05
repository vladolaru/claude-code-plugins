---
description: Update a PR branch with its latest base branch and resolve merge conflicts — by PR number or URL, or the current branch's PR
---

You update a pull request's branch with the latest commits from its base branch, resolve any merge conflicts, verify the result, and push, the same job as GitHub's "Update branch" button plus the conflict work it cannot do.

**RULE 0: Merge the base into the branch.** A merge keeps review threads anchored and pushes without force. Rebase only when the user's input asks for one, and then push with `--force-with-lease`.

**RULE 1: The base is the PR's `baseRefName`**, not an assumed `trunk` or `main`. Stacked and release PRs target other branches.

**RULE 2: Run code that came with the PR only with consent when someone else wrote it.** Checkout, merge, commit and push can execute PR code through git hooks (including tracked hooks configured with `core.hooksPath`); installs, regeneration, builds, tests and lint execute it too. Step 1 establishes consent before any of these operations. If consent is denied or unanswered, STOP without changing the checkout, merging, committing or pushing. Do not disable hooks to work around denial.

**One command at a time from Step 3 to Step 6:** merge, resolve, install, check, commit and push each depend on the one before. Send each as its own tool call and read its result before sending the next; never batch them in parallel.

**Hooks:** `git checkout`, `git merge`, `git commit` and `git push` run the repository's git hooks, which can install dependencies or run checks for minutes. Give those commands a long timeout or wait for them in the background, and let each finish before the next step. Do not redo work a hook already did, and never skip hooks with `--no-verify`.

**Expected failures:** git and gh commands fail for normal reasons (network, auth, a branch checked out in another worktree). Report the error with the command that failed and STOP.

## Step 1: Find the PR and establish execution consent

**GitHub CLI:** check that `gh` can reach this repository with `gh repo view --json nameWithOwner`; `gh` picks the host from the git remote. If it can, `GH_CMD` is `gh`. If it cannot reach the host (a GitHub Enterprise server behind a proxy, for example) and the user's instructions or skills name a wrapper, proxy, or environment for that host, use it to build `GH_CMD` (for example, `gh` run with the proxy in `HTTPS_PROXY`) and repeat the check. Otherwise STOP and report the error. Run every later GitHub call in this command with `GH_CMD`. Store the `nameWithOwner` it returns as `BASE_REPO`.

Record where the user is before anything moves: `START_BRANCH=$(git branch --show-current)`.

**Parse arguments:** `$ARGUMENTS`

- A PR number (`3817`, `#3817`) or PR URL: use it as `<PR>` for the metadata read below. Do not run `/switch-to` yet.
- Empty: resolve the current branch's PR with `$GH_CMD pr view --json number,url,state`. When `state` is `OPEN`, use it and say so in one line ("No PR given; updating #3817 for the current branch"). For a closed or merged PR, no PR, or a failed command, ask the user whether to allow hooks and checks while merging the repository's default branch (`$GH_CMD repo view --json defaultBranchRef`) into the current branch anyway. If yes, use the default branch as `baseRefName` and the current branch as `headRefName` (STOP if detached), skip the PR metadata and PR-specific consent/remote setup below, treat that approval as consent for hooks and checks on the user's current branch, and push in Step 6 only if the branch has an upstream. Record its configured upstream remote and destination branch explicitly; do not assume the local name is the destination.

Then read the PR's metadata:

```bash
$GH_CMD pr view <PR> --json number,url,state,author,baseRefName,headRefName,headRepositoryOwner,headRepository,isCrossRepository,maintainerCanModify
```

STOP if `state` is not `OPEN`, or if the PR URL's host and base repository do not match this checkout's `BASE_REPO` and GitHub host.

Establish execution consent (RULE 2). When `author.login` matches the user's login (`$GH_CMD api user --jq .login`), proceed. Otherwise use existing explicit authorization for this PR's code if the session has it, or ask once: "#<number> is by @<author>. Updating it can run its code through checkout, merge, commit and push hooks, plus installs and checks. Allow that on this machine?" Only proceed with a yes; denial or no answer means STOP before any mutation.

Before switching, inspect `MERGE_HEAD` and the rebase state directories using `git rev-parse --git-path` (also works in linked worktrees). A rebase in progress means STOP. With a merge in progress, do not switch, stash or synchronize: confirm the current branch's PR (`$GH_CMD pr view --json url`) is the selected PR, or STOP without changing the merge. Then use the resume path in Step 2. For the no-PR fallback, STOP on an existing merge because its PR identity cannot be confirmed.

With no merge in progress and an explicit PR argument, run `/switch-to <argument>` and follow it through, preserving this execution consent. It handles dirty state and remote synchronization. Note any stash. If it stops, STOP.

For every PR, including the no-argument and resume paths, apply **Resolve PR remotes** in `/switch-to` Step 2A using the metadata above. Set `HEAD_REMOTE` to its `REMOTE_NAME`, and keep its `BASE_REMOTE`. This must run even when no checkout was needed; never assume an owner-named remote exists. If the fork is not the user's and `maintainerCanModify` is false, STOP before merging and explain that the PR cannot be updated with these permissions.

## Step 2: Check the working tree

- `git rev-parse -q --verify MERGE_HEAD` succeeds: preserve the index and working tree and go to Step 3 to initialize and validate the merge refs. Do not fast-forward, stash, or start another merge.
- `git status --porcelain` is not empty (only reachable when Step 1 did not run `/switch-to`): ask whether to stash (`git stash push --include-untracked`), or stop so the user can commit. Note the stash for Step 8.
- The branch's upstream has commits the local branch lacks (`git fetch` the upstream, then `git rev-list --count HEAD..@{upstream}`): `git merge --ff-only @{upstream}`. If it cannot fast-forward, STOP: the local and remote branch diverged, and the user decides which wins.

## Step 3: Merge the base

Use `BASE_REMOTE` resolved in Step 1. For the no-PR fallback, resolve it by matching the GitHub host and exact `BASE_REPO` path against the remote URL (allow SSH or HTTPS and an optional `.git` suffix). If none matches, STOP and report the missing base remote.

The explicit refspec updates the remote-tracking ref even in a single-branch clone:

```bash
git fetch <BASE_REMOTE> +refs/heads/<baseRefName>:refs/remotes/<BASE_REMOTE>/<baseRefName>
BASE_TIP=$(git rev-parse refs/remotes/<BASE_REMOTE>/<baseRefName>)
```

Record `PRE_MERGE=$(git rev-parse HEAD)` on both fresh and resumed paths, before any merge commit. For an unfinished merge, `HEAD` is still its original first parent; do not use a commit subject or a potentially stale `ORIG_HEAD` to identify it.

**Resume validation:** if `MERGE_HEAD` exists, read it from `git rev-parse --git-path MERGE_HEAD`. Require exactly one commit and require its object ID to equal `BASE_TIP`. A different commit, multiple merge heads, or a base that advanced since the merge started means STOP, preserving the merge and reporting both IDs. Do not resolve or commit a merge against a different base.

Bring the local base branch up to date too, so local diffs against `<baseRefName>` match the PR. This copies the ref just fetched, creates the branch if it is missing, and only fast-forwards:

```bash
git fetch . refs/remotes/<BASE_REMOTE>/<baseRefName>:refs/heads/<baseRefName>
```

Git refuses the update when the local base has commits of its own (`non-fast-forward`) or is checked out in another worktree. Leave it as it is, carry on, and name the reason in the report.

For a validated resumed merge, go to Step 4 even if all conflicts are already staged. Otherwise count `git rev-list --count HEAD..$BASE_TIP`. If the count is 0, report "Already up to date with `<baseRefName>`" and the local base's state, then STOP. Otherwise merge:

```bash
git merge --no-edit -m "Merge branch '<baseRefName>' into <headRefName>" "$BASE_TIP"
```

The message names the PR's own head branch, which can differ from the local name `/switch-to` chose; git keeps it for a merge that stops on conflicts.

A clean merge goes straight to Step 5.

## Step 4: Resolve conflicts

List them with `git diff --name-only --diff-filter=U`. For each file, read what each side meant before editing: `git log --merge --oneline -- <file>` names the commits on both sides, and `git diff $(git merge-base HEAD MERGE_HEAD) MERGE_HEAD -- <file>` shows the base's change.

- **Code and prose changed on both sides:** keep both intents in one result. Neither side wins wholesale.
- **Lockfiles and generated files** (`pnpm-lock.yaml`, `composer.lock`, build output, generated adapters): take the base version with `git checkout --theirs -- <file>` (in a merge, "theirs" is the base), then regenerate it with the repository's own command so the PR's changes are reapplied.
- **Modified on one side, deleted or moved on the other:** find where the base moved the code (`git log --diff-filter=DR --oneline MERGE_HEAD -- <file>`) and port the PR's change there.
- **Combining both intents needs a choice neither side made** (the order two changes apply in, which default wins): make it, and name it in the report next to the file.
- **The two sides want contradictory behavior** (both changed the same value or rule to different results): STOP before committing. Show both versions and ask which behavior the PR should keep.

Stage each resolved file. Before moving on, `git diff --cached --check` must report no conflict markers.

## Step 5: Verify the merged result

A merge that git reports as clean can still break the build: the base may have renamed or removed something the PR's new code uses. Collect the names the base removed or renamed: the `-` lines of `git diff $PRE_MERGE...$BASE_TIP` that define a function, class, constant, export, or hook whose name does not come back on a `+` line. A changed definition is not a removal. Grep the PR's changed files (`git diff --name-only $BASE_TIP...$PRE_MERGE`) for each one. A hit in code is breakage; a hit in prose such as a changelog is not.

If the merge changed a lockfile or dependency manifest, bring dependencies in line before the checks. A post-merge hook may have done it already, but git skips that hook after a conflicted merge. Use the install command the repository documents, or the lockfile's frozen install (`pnpm install --frozen-lockfile`, `composer install`). If they are not refreshed, the report says the checks ran against stale dependencies.

Then run the checks the repository's `AGENTS.md` or `CLAUDE.md` names for the files the PR touches and the files that conflicted. When neither names any, use the test and lint entry points the project has: scripts in its manifest or CI config (`package.json`, `composer.json`, `Makefile`, `.github/workflows/`), or test files and directories at the root. Report "no checks found" only when there are none of these. Fix what breaks; a fix goes in the merge commit when the merge is still uncommitted, otherwise in its own commit.

## Step 6: Commit and push

Conclude a conflicted merge with `git commit --no-edit`. If commit signing fails, leave the merge staged, report it, and STOP without pushing.

Push to the branch the PR is built from, without force (the rebase in RULE 0 is the one exception):

- Every PR, same-repository or fork: `git push <HEAD_REMOTE> HEAD:refs/heads/<headRefName>`. Recheck that all push URLs still identify the PR head repository using the Step 1 remote rules. The destination is the metadata head branch even when the local branch is `pr-<number>`.
- No-PR fallback: push to the recorded upstream remote with `HEAD:refs/heads/<upstream branch>`, or report local-only if there is no upstream.
- Push rejected because someone pushed to the PR branch in the meantime: STOP and report both heads. Do not force.

## Step 7: Report

```
<"No PR given; updating #<number> for the current branch", when Step 1 resolved the PR itself>
Updated #<number> with <baseRefName>: merged <N> commits, pushed <short sha>.

Conflicts:
  <file> — <how it was resolved, one line>
Dependencies: <refreshed by <command> | refreshed by a hook | unchanged by the merge | stale: <why>>
Verification: <commands run and their result>
Local <baseRefName>: <fast-forwarded to <short sha> | created at <short sha> | already current | not updated: <git's reason>>
Git range for the changes: <PRE_MERGE>...<HEAD>
```

Write "Conflicts: none" for a clean merge, and list any fix Step 5 needed under its own `Post-merge fixes:` line. Say plainly what was not verified.

## Step 8: Return to where the user was

If the current branch is no longer `START_BRANCH`, or Step 1 or Step 2 stashed changes, ask whether to switch back (`git checkout <START_BRANCH>`) and restore the stash (`git stash pop`). Do neither without a yes, and end by saying which branch the user is on and whether a stash remains.

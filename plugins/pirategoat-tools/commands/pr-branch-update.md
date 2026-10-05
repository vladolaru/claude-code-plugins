---
description: Update a PR branch with its latest base branch and resolve merge conflicts — by PR number or URL, or the current branch's PR
---

You update a pull request's branch with the latest commits from its base branch, resolve any merge conflicts, verify the result, and push, the same job as GitHub's "Update branch" button plus the conflict work it cannot do.

**RULE 0: Merge the base into the branch.** A merge keeps review threads anchored and pushes without force. Rebase only when the user's input asks for one, and then push with `--force-with-lease`.

**RULE 1: The base is the PR's `baseRefName`**, not an assumed `trunk` or `main`. Stacked and release PRs target other branches.

**RULE 2: Run code that came with the PR only with consent when someone else wrote it.** Installs, regeneration, builds, tests and lint all execute the PR's code. Step 1 sets `RUN_PR_CODE`; when it is no, skip those commands and report the checks as not run.

**Expected failures:** git and gh commands fail for normal reasons (network, auth, a branch checked out in another worktree). Report the error with the command that failed and STOP.

## Step 1: Find the PR and get onto its branch

**GitHub CLI:** check that `gh` can reach this repository with `gh repo view --json nameWithOwner`; `gh` picks the host from the git remote. If it can, `GH_CMD` is `gh`. If it cannot reach the host (a GitHub Enterprise server behind a proxy, for example) and the user's instructions or skills name a wrapper, proxy, or environment for that host, use it to build `GH_CMD` (for example, `gh` run with the proxy in `HTTPS_PROXY`) and repeat the check. Otherwise STOP and report the error. Run every later GitHub call in this command with `GH_CMD`. Store the `nameWithOwner` it returns as `BASE_REPO`.

**Parse arguments:** `$ARGUMENTS`

- A PR number (`3817`, `#3817`) or PR URL: run `/switch-to <argument>` and follow it through. It handles a dirty tree, fork remotes, and pulling new remote commits. If it stops, STOP.
- Empty: resolve the current branch's PR with `$GH_CMD pr view --json number,url,state`. When `state` is `OPEN`, use it and say so in one line ("No PR given; updating #3817 for the current branch"). For a closed or merged PR, no PR, or a failed command, ask the user whether to merge the repository's default branch (`$GH_CMD repo view --json defaultBranchRef`) into the current branch anyway. If yes, use the default branch as `baseRefName`, skip the metadata read below, set `RUN_PR_CODE` to yes (the branch is the user's own work), and push in Step 6 only if the branch has an upstream.

Then read the PR's metadata:

```bash
$GH_CMD pr view <PR> --json number,url,state,author,baseRefName,headRefName,headRepositoryOwner,headRepository,isCrossRepository,maintainerCanModify
```

STOP if `state` is not `OPEN`.

Set `RUN_PR_CODE` (RULE 2). When `author.login` matches the user's login (`$GH_CMD api user --jq .login`), it is yes. Otherwise ask once: "#<number> is by @<author>. Run its installs and checks on this machine?" and use the answer.

## Step 2: Check the working tree

- `git rev-parse -q --verify MERGE_HEAD` succeeds: a merge is already in progress. Confirm with `git log -1 --format=%s MERGE_HEAD` that it merges the PR's base, then go to Step 4 with its conflicts. A merge of anything else, or a rebase in progress (`.git/rebase-merge` or `.git/rebase-apply` exists): STOP and describe the state.
- `git status --porcelain` is not empty (only reachable when Step 1 did not run `/switch-to`): ask whether to stash (`git stash push --include-untracked`), or stop so the user can commit.
- The branch's upstream has commits the local branch lacks (`git fetch` the upstream, then `git rev-list --count HEAD..@{upstream}`): `git merge --ff-only @{upstream}`. If it cannot fast-forward, STOP: the local and remote branch diverged, and the user decides which wins.

## Step 3: Merge the base

Find the remote for the base repository: the remote whose URL contains `BASE_REPO`, usually `origin`. Call it `BASE_REMOTE`. If no remote URL matches, ask the user which remote holds the base.

The explicit refspec updates the remote-tracking ref even in a single-branch clone:

```bash
git fetch <BASE_REMOTE> +refs/heads/<baseRefName>:refs/remotes/<BASE_REMOTE>/<baseRefName>
git rev-list --count HEAD..<BASE_REMOTE>/<baseRefName>
```

Bring the local base branch up to date too, so local diffs against `<baseRefName>` match the PR. This copies the ref just fetched, creates the branch if it is missing, and only fast-forwards:

```bash
git fetch . refs/remotes/<BASE_REMOTE>/<baseRefName>:refs/heads/<baseRefName>
```

Git refuses the update when the local base has commits of its own (`non-fast-forward`) or is checked out in another worktree. Leave it as it is, carry on, and name the reason in the report.

If the count is 0, report "Already up to date with `<baseRefName>`" and the local base's state, then STOP. Otherwise record `PRE_MERGE=$(git rev-parse HEAD)` and merge:

```bash
git merge --no-edit <BASE_REMOTE>/<baseRefName>
```

A clean merge goes straight to Step 5.

## Step 4: Resolve conflicts

List them with `git diff --name-only --diff-filter=U`. For each file, read what each side meant before editing: `git log --merge --oneline -- <file>` names the commits on both sides, and `git diff $(git merge-base HEAD MERGE_HEAD) MERGE_HEAD -- <file>` shows the base's change.

- **Code and prose changed on both sides:** keep both intents in one result. Neither side wins wholesale.
- **Lockfiles and generated files** (`pnpm-lock.yaml`, `composer.lock`, build output, generated adapters): take the base version with `git checkout --theirs -- <file>` (in a merge, "theirs" is the base), then regenerate it with the repository's own command so the PR's changes are reapplied. When `RUN_PR_CODE` is no, leave the file unresolved and STOP before committing: say it needs regenerating.
- **Modified on one side, deleted or moved on the other:** find where the base moved the code (`git log --diff-filter=DR --oneline MERGE_HEAD -- <file>`) and port the PR's change there.
- **Combining both intents needs a choice neither side made** (the order two changes apply in, which default wins): make it, and name it in the report next to the file.
- **The two sides want contradictory behavior** (both changed the same value or rule to different results): STOP before committing. Show both versions and ask which behavior the PR should keep.

Stage each resolved file. Before moving on, `git diff --cached --check` must report no conflict markers.

## Step 5: Verify the merged result

A merge that git reports as clean can still break the build: the base may have renamed or removed something the PR's new code uses. Collect the names the base removed or renamed: the `-` lines of `git diff $PRE_MERGE...<BASE_REMOTE>/<baseRefName>` that define a function, class, constant, export, or hook. Grep the PR's changed files (`git diff --name-only <BASE_REMOTE>/<baseRefName>...$PRE_MERGE`) for each one. A hit in code is breakage; a hit in prose such as a changelog is not.

When `RUN_PR_CODE` is no, stop here: report the checks as not run, then go to Step 6.

Then run the checks the repository's `AGENTS.md` or `CLAUDE.md` names for the files the PR touches and the files that conflicted. When neither names any, use the test and lint entry points the project has: scripts in its manifest or CI config (`package.json`, `composer.json`, `Makefile`, `.github/workflows/`), or test files and directories at the root. Report "no checks found" only when there are none of these. Fix what breaks; a fix goes in the merge commit when the merge is still uncommitted, otherwise in its own commit.

## Step 6: Commit and push

Conclude a conflicted merge with `git commit --no-edit`. If commit signing fails, leave the merge staged, report it, and STOP without pushing.

Push to the branch the PR is built from, without force (the rebase in RULE 0 is the one exception):

- Same-repository PR: `git push origin HEAD`.
- Fork PR (`isCrossRepository` is true): `git push <fork remote> HEAD:<headRefName>`, where the fork remote is the one `/switch-to` set up (named after `headRepositoryOwner`). If `maintainerCanModify` is false and the fork is not the user's, the push will be refused; report that the update is local only.
- Push rejected because someone pushed to the PR branch in the meantime: STOP and report both heads. Do not force.

## Step 7: Report

```
Updated #<number> with <baseRefName>: merged <N> commits, pushed <short sha>.

Conflicts:
  <file> — <how it was resolved, one line>
Verification: <commands run and their result>
Local <baseRefName>: <fast-forwarded to <short sha> | created at <short sha> | already current | not updated: <git's reason>>
Git range for the changes: <PRE_MERGE>...<HEAD>
```

Write "Conflicts: none" for a clean merge, and list any fix Step 5 needed under its own `Post-merge fixes:` line. Say plainly what was not verified.

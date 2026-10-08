---
name: pr-branch-update
description: "Update a PR branch with its latest base branch and resolve merge conflicts - by PR number or URL, or the current branch's PR"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/pr-branch-update.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


You update a pull request's branch with the latest commits from its base branch, resolve any merge conflicts, verify the result, and push, the same job as GitHub's "Update branch" button plus the conflict work it cannot do. You follow named rules from `$pirategoat-tools:switch-to`; read that command first.

**RULE 0: Merge the base into the branch.** A merge keeps review threads anchored and pushes without force. Rebase only when the user's input asks for one, and then push with `--force-with-lease`.

**RULE 1: The base is the PR's `baseRefName`**, not an assumed `trunk` or `main`. Stacked and release PRs target other branches.

**RULE 2: Run code that came with the PR only with consent when someone else wrote it.** Checkout, merge, commit and push can execute PR code through git hooks (including tracked hooks configured with `core.hooksPath`); installs, regeneration, builds, tests and lint execute it too. Step 1 establishes consent with `$pirategoat-tools:switch-to`'s **Execution consent** rule before any of these operations. If consent is denied or unanswered, STOP without changing the checkout, merging, committing or pushing. Do not disable hooks to work around denial.

**Values are data:** follow `$pirategoat-tools:switch-to`'s **Values are data** rule. Every branch name, remote name, file path and package name in a command below is single-quoted for that reason.

**One command at a time:** from Step 3 to Step 6 each command depends on the one before; send it as its own tool call and read its result first. `git checkout`, `git merge`, `git commit` and `git push` run hooks that can install dependencies or run checks for minutes: give them a long timeout or wait in the background, do not redo work a hook already did, and never skip hooks with `--no-verify`.

**Expected failures:** git and gh commands fail for normal reasons (network, auth, a branch checked out in another worktree). Report the error with the command that failed and STOP. Once the checkout has changed (a switch, stash, sync or merge), every exit, a STOP included, ends through Step 7's report and Step 8.

## Step 1: Find the PR and pin what the update needs

**GitHub CLI:** set `GH_CMD` with `$pirategoat-tools:switch-to`'s **GitHub CLI** rule, checking with `gh repo view --json nameWithOwner,url,defaultBranchRef`. Store `nameWithOwner` as `BASE_REPO`, the host of `url` as the checkout's host, and `defaultBranchRef` for the no-PR fallback.

Record where the user is before anything moves: `START_BRANCH=$(git branch --show-current)` and `START_HEAD=$(git rev-parse HEAD)`. Inspect the merge and rebase state with `git rev-parse --git-path` (it works in linked worktrees): a rebase in progress means STOP, and an existing `MERGE_HEAD` sets `RESUMING`. While `RESUMING`, nothing switches, stashes, syncs, or starts another merge.

**Parse arguments:** `${CODEX_SKILL_ARGUMENTS}` is a PR number (`3817`, `#3817`), a PR URL, or empty. Read the PR, passing the argument as `'<PR>'`; with no argument, leave `'<PR>'` out so `gh` resolves the current branch's PR:

```bash
$GH_CMD pr view '<PR>' --json number,url,state,title,body,author,baseRefName,headRefName,headRepositoryOwner,headRepository,isCrossRepository,maintainerCanModify
```

- With no argument, an `OPEN` PR is the one to update; say so in one line ("No PR given; updating #3817 for the current branch"). `no pull requests found`, or a PR that is not `OPEN`, starts the **no-PR fallback** below.
- With an argument, STOP if `state` is not `OPEN`.
- Any other failure (network, authentication) is an error, not a missing PR: report it with the command and STOP, since an open PR may target a different base.
- STOP if the PR URL's host and base repository do not match the checkout's host and `BASE_REPO`.

The `$pirategoat-tools:switch-to` rules below use its names; set them from this read: `PR_HOST` is the host of `url`, `PR_NUMBER` is `number`, `HEAD_BRANCH` and `BASE_BRANCH` are `headRefName` and `baseRefName`, `HEAD_OWNER` and `HEAD_REPO` are `headRepositoryOwner.login` and `headRepository.name`, and `CURRENT_BRANCH` is `START_BRANCH`.

Establish execution consent with `$pirategoat-tools:switch-to`'s **Execution consent** rule. When you ask, name this command's wider reach: "#<number> is by @<author>. Updating it can run its code through checkout, merge, commit and push hooks, plus installs and checks. Allow that on this machine?"

While `RESUMING` with an explicit argument, the current branch must be this PR's head: STOP unless the current branch's PR (`$GH_CMD pr view --json url`) is the selected one. Without an argument, the read above already resolved it from the current branch.

Apply `$pirategoat-tools:switch-to`'s **Resolve PR remotes** to the metadata: `HEAD_REMOTE` is its `REMOTE_NAME`, and keep its `BASE_REMOTE`. If `isCrossRepository` is true, the fork is not the user's, and `maintainerCanModify` is false, STOP: the PR cannot be updated with these permissions.

Unless `RESUMING`, when the PR's branch is not checked out (an explicit argument), check it out with `$pirategoat-tools:switch-to`'s **Choose the local branch name**, Step 3 (dirty working tree) and Step 4 (switch), using the metadata, consent and remotes above. Skip its Steps 5 to 7: Steps 2 and 3 here sync and fetch. Note any stash. If it stops, STOP; when it stopped after restoring its own stash, Step 8 has no stash to restore.

**No-PR fallback:** offer to merge the default branch into the current branch instead; STOP if detached or `RESUMING`, since no PR confirms that merge's identity. Set what the PR path sets:

- `baseRefName` is the default branch and `headRefName` the current branch.
- `BASE_REMOTE` comes from the **Resolve PR remotes** `BASE_REMOTE` rule, with the checkout's host as `PR_HOST` and `BASE_REPO` as the repository.
- `HEAD_REMOTE` is the branch's own remote: its configured upstream remote (`branch.<name>.remote`) only when `branch.<name>.merge` names a branch of the same name. An upstream with another name, such as the `origin/trunk` or `origin/release/X` the branch was created from, is not its own: syncing with it would look like divergence, and pushing to it would update a shared branch. Otherwise `HEAD_REMOTE` stays unset.

Then ask: "No open PR for `<branch>`. Merge `<default branch>` into it anyway, running this repository's hooks and checks, and <push to `<HEAD_REMOTE>/<branch>` | keep the result local>?", choosing "push" exactly when `HEAD_REMOTE` is set. A yes is consent for hooks and checks on the user's branch; skip the PR consent, remotes and checkout above.

## Step 2: Check the working tree and sync with GitHub

- `RESUMING`: preserve the index and working tree and go to Step 3.
- `git status --porcelain` is not empty (only when Step 1 did not check out): ask whether to stash (`git stash push --include-untracked`), or stop so the user can commit. Note the stash for Step 8.

Bring the local branch up to what GitHub has, from the PR's head branch rather than `@{upstream}`, which may be missing or track another branch. Without `HEAD_REMOTE` there is nothing to sync.

```bash
git fetch '<HEAD_REMOTE>' '+refs/heads/<headRefName>:refs/remotes/<HEAD_REMOTE>/<headRefName>'
PUSHED_TIP=$(git rev-parse 'refs/remotes/<HEAD_REMOTE>/<headRefName>')
```

If `git rev-list --count HEAD..$PUSHED_TIP` is not 0, run `git merge --ff-only $PUSHED_TIP`. If it cannot fast-forward, STOP: the local and pushed branches diverged, and the user decides which wins. Local commits that are not pushed yet stay and go out with the push in Step 6.

## Step 3: Merge the base

The explicit refspec updates the remote-tracking ref even in a single-branch clone:

```bash
git fetch '<BASE_REMOTE>' '+refs/heads/<baseRefName>:refs/remotes/<BASE_REMOTE>/<baseRefName>'
BASE_TIP=$(git rev-parse 'refs/remotes/<BASE_REMOTE>/<baseRefName>')
```

Set `PRE_MERGE` to `PUSHED_TIP`, the branch as GitHub has it, or to `HEAD` when Step 2 fetched none. While `RESUMING`, `HEAD` is still the unfinished merge's first parent.

While `RESUMING`, require exactly one `MERGE_HEAD` commit (read it from `git rev-parse --git-path MERGE_HEAD`) equal to `BASE_TIP`. Anything else means STOP, preserving the merge and reporting both IDs: never resolve or commit a merge against a different base.

Bring the local base branch up to date too, so local diffs against `<baseRefName>` match the PR. Note its commit first (`git rev-parse -q --verify 'refs/heads/<baseRefName>'`), because the update prints nothing and the report says whether it moved:

```bash
git fetch . 'refs/remotes/<BASE_REMOTE>/<baseRefName>:refs/heads/<baseRefName>'
```

Git refuses the update when the local base has commits of its own (`non-fast-forward`) or is checked out in another worktree. Leave it as it is, carry on, and name the reason in the report.

A valid resume goes to Step 4, even if all conflicts are already staged. Otherwise:

- `PRE_MERGE` contains the base (`git merge-base --is-ancestor $BASE_TIP $PRE_MERGE`): go to Step 7, whose report opens "Already up to date with `<baseRefName>`" and keeps the lines that apply, then Step 8.
- `HEAD` contains the base and `PRE_MERGE` does not: an earlier run merged without pushing. Skip the merge and go to Step 5.
- Otherwise merge, naming the PR's head branch, which can differ from the local name `$pirategoat-tools:switch-to` chose:

```bash
git merge --no-edit -m 'Merge branch '\''<baseRefName>'\'' into <headRefName>' "$BASE_TIP"
```

A clean merge goes straight to Step 5.

## Step 4: Resolve conflicts

List them with `git diff --name-only --diff-filter=U`. For each file, read what each side meant before editing: `git log --merge --oneline -- '<file>'` names the commits on both sides, and `git diff $(git merge-base HEAD MERGE_HEAD) MERGE_HEAD -- '<file>'` shows the base's change.

- **Code and prose changed on both sides:** keep both intents in one result. Neither side wins wholesale.
- **Lockfiles** (`pnpm-lock.yaml`, `composer.lock`): a PR can change locked versions without touching the manifest, such as a security or transitive update, and regenerating from the base keeps the base's older versions without any error. Before resolving, list the packages whose locked version the PR changed: `git diff $(git merge-base HEAD MERGE_HEAD) HEAD -- '<lockfile>'`. Take the base version (`git checkout --theirs -- '<file>'`), update it for the merged manifest (`pnpm install` does; Composer needs `composer update '<name>'` for each package whose constraint the merge changed, since `composer install` only warns), then move each listed package back to the PR's version with the package manager's targeted update, leaving the manifest unchanged (for example, `composer update '<name>' --with '<name>:<version>'`). Check that each PR version is in the result; when the base now rules one out, name it in the report next to the file.
- **Other generated files** (build output, generated adapters): take the base version with `git checkout --theirs -- '<file>'` (in a merge, "theirs" is the base), then regenerate it with the repository's own command so the PR's changes are reapplied.
- **Modified on one side, moved on the other:** find where the base moved the code (`git log --diff-filter=DR --oneline MERGE_HEAD -- '<file>'`) and port the PR's change there. Code the base deleted outright is the contradictory case below.
- **Combining both intents needs a choice neither side made** (the order two changes apply in, which default wins): make it, and name it in the report next to the file.
- **The two sides want contradictory behavior** (both changed the same value or rule to different results, or one side deleted what the other changed): STOP before committing. Resolve and stage the other files first, leave the merge in progress, then show both versions and ask which behavior the PR should keep. The answer resumes the merge, and so does rerunning this command.

Git merged the rest of each conflicted file on its own. Check those hunks still fit the resolution: they can carry the other half of one side's intent, such as a fixture only a deleted test used. Stage each resolved file. Before moving on, `git diff --cached --check -- '<resolved file>'...` must print no `leftover conflict marker` lines; whitespace warnings either side brought in are not this command's concern.

## Step 5: Verify the merged result

A merge that git reports as clean can still break the build. The PR's files are `git diff --name-only $BASE_TIP...HEAD`, the PR's side on every path. Check two things against them:

- **Removed names:** from the lines the base deleted (`git diff -U0 $PRE_MERGE...$BASE_TIP | grep '^-'`), take the top-level definitions: functions, classes, methods, exported symbols, PHP constants and hook names. A name is removed when `git grep -w '<name>' $BASE_TIP` no longer finds it defined anywhere, and it is breakage when the PR's code, not its prose, still uses it.
- **New conventions:** the base applied a rule to every existing case in a file the PR touches (`git diff $PRE_MERGE...$BASE_TIP -- '<PR file>'`), such as a new required argument, wrapper, import, or option on every test. New code the PR adds there must follow it too, even where git merged it cleanly.

If `git diff --name-only $START_HEAD` lists a lockfile or dependency manifest (it compares with the working tree, so a conflicted merge that is not committed yet counts), bring dependencies in line before the checks. A post-merge hook may have done it already, but git skips that hook after a conflicted merge. Use the install command the repository documents, or the lockfile's frozen install (`pnpm install --frozen-lockfile`, `composer install`). If they are not refreshed, the report says the checks ran against stale dependencies.

Then run the checks the repository's `AGENTS.md` or `CLAUDE.md` names for the files the PR touches and the files that conflicted. When neither names any, use the test and lint entry points the project has: scripts in its manifest or CI config (`package.json`, `composer.json`, `Makefile`, `.github/workflows/`), or test files and directories at the root. Report "no checks found" only when there are none of these. Run them on the PR's files and the conflicted files directly: a script that picks its files from git state (unstaged changes, or a diff against a merge-base) checks nothing or everything around a merge, and a check whose configuration does not cover the PR's files is not applicable. Neither counts as a pass.

Fix what the merge broke; a fix goes in the merge commit when the merge is still uncommitted, otherwise in its own commit. A failure that was already there before the merge (the same check fails at the merge's first parent, or at `HEAD` before Step 3 when this run made no merge), or that comes from the base's own code, is reported, not fixed here.

## Step 6: Commit and push

Conclude a conflicted merge with `git commit --no-edit`. If commit signing fails, leave the merge staged, report it, and STOP without pushing. Hooks can add files to a commit, so check the merge commit: `git show --remerge-diff --stat HEAD` must list only files you resolved or fixed. If it lists others, STOP and report them before pushing.

Push without force (RULE 0's rebase is the one exception) to the branch the PR is built from: `git push '<HEAD_REMOTE>' 'HEAD:refs/heads/<headRefName>'`. For a PR, first recheck that all of `HEAD_REMOTE`'s push URLs still identify the PR head repository under **Resolve PR remotes**; the destination is the metadata head branch even when the local branch is `pr-<number>`. Without `HEAD_REMOTE`, do not push: the result is local-only. A rejected push means someone pushed to the branch meanwhile: STOP and report both heads.

## Step 7: Report

```
Updated <#<number> | `<branch>`, which has no PR,> with <baseRefName>: <merged <N> commits | found <N> base commits merged locally but not on GitHub>; <pushed <short sha> | not pushed: <why>>.

Synced: <fast-forwarded <n> commits from GitHub before merging; only when Step 2 did>
Conflicts: <none | one line per file: <file> - <how it was resolved>>
Post-merge fixes: <what Step 5 fixed; only when it fixed something>
Dependencies: <refreshed by <command> | refreshed by a hook | refreshed while resolving conflicts | unchanged | stale: <why>>
Verification: <commands run and their result>
Not verified: <checks not run or not applicable, and why>
Local <baseRefName>: <fast-forwarded to <short sha> | created at <short sha> | already current | not updated: <git's reason>>
PR text: <still accurate | stale: <title or body lines the merge made wrong, and files that left the PR's diff>>
Git range for the changes: <PRE_MERGE>...<HEAD>
```

`<N>` is the number of base commits the update brings: `git rev-list --count $PRE_MERGE..$BASE_TIP`. For `PR text:`, compare the title and body from Step 1 with what the merge changed; when they are stale, suggest `$pirategoat-tools:pr-update` and do not edit them. When the command stopped partway, open with `Stopped updating <#<number> | `<branch>`>: <why>` and keep only the lines that apply.

## Step 8: Return to where the user was

With a merge still in progress (a STOP in Step 4, 5 or 6), do not offer to switch: git cannot check out over unmerged paths, and switching would discard the resolution. Say which branch the user is on and that the merge is in progress. Otherwise, if the user is no longer where they started, or Step 1 or Step 2 stashed changes, ask whether to switch back and restore the stash (`git stash pop`). Switch back with `git checkout '<START_BRANCH>'`, or with `git checkout --detach <START_HEAD>` when the command started on a detached HEAD (`START_BRANCH` empty). Do neither without a yes, and end by saying which branch the user is on and whether a stash remains.

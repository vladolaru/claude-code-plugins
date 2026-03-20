---
name: create-github-pr
description: Use when creating GitHub pull requests - provides structured workflow for pre-flight checks, context gathering, content generation, user approval gate, and testing steps synthesis from related PRs.
---

# Create GitHub PR

Structured workflow for creating high-quality GitHub pull requests with proper context, testing steps, and user approval.

## When to Use

- Creating a new pull request
- Asked to "open a PR" or "submit for review"
- Branch is ready to merge

## PR Template

**FIRST**: Check for `.github/PULL_REQUEST_TEMPLATE.md`
- If template exists: use it, fill all sections, complete checklist
- If NO template: use description template from Phase 2 below
- Template sections override generic guidelines

## PR Creation Workflow

### Phase 0 — Pre-flight Checks

HALT if any fail:

1. `git branch --show-current` - Must NOT be on `trunk` (or default branch)
2. `git status` - No uncommitted changes
3. Identify `<base_branch>`:
   - Run: `git log --oneline --decorate --all --graph -20` to visualize branch structure
   - Run: `git merge-base --fork-point <likely_base> HEAD` to find fork point
   - The base branch is typically where the current branch was created from (may differ from repo default)
4. `git log <base_branch>..HEAD` - Has commits to include
5. `git fetch && git status` - Up to date with remote
6. **Check for existing PR:**
   ```bash
   gh pr view --json url,state 2>/dev/null || echo "NO_EXISTING_PR"
   ```
   - If a PR already exists, show the URL and state. Ask user whether to update the existing PR or create a new one. HALT until answered.

### Phase 1 — Gather Context

Run in parallel:
- `git log --oneline <base_branch>..HEAD` - List commits
- `git diff <base_branch>...HEAD` - Code changes
- `git rev-parse --abbrev-ref HEAD` - Branch name

### Phase 2 — Generate PR Content

**Issue Detection from Branch Name:**
- Linear issue: `feature/PROJ-123-desc` → `Closes PROJ-123`
- GitHub issue: `fix/issue-123-bug` → `Closes #123`
- Common patterns: `WOOPLUG-123`, `WOOPMNT-456`, `TRAPLAT-789`, `DEVELOPER-321`

Wrap analysis in `<pr_analysis>` tags:
```
<pr_analysis>
- Branch name: What issue/feature does it reference?
- Issue number: Extract from branch (Linear ID or GitHub #number)
- Commits: List significant ones (skip style/misc fixes)
- Code changes: What functionality changed?
- Testing: What coverage exists? What manual testing needed?
</pr_analysis>
```

**Title rules** (NOTE: PR titles do NOT use Conventional Commits format):
- DO NOT include issue number in title
- Use imperative verb (Add, Fix, Update, etc.)
- Be specific about the change

**Description template:**
```markdown
Closes <ISSUE_NUMBER>

### Description
[Concise summary for audience familiar with codebase]

**Key commits:**
- [Description] (<short_hash>)
- [Description] (<short_hash>)

### Testing Steps
1. [Step based on code changes and unit tests]
2. [Expected outcome]

---
🤖 Generated with [Claude Code](https://claude.ai/code)
```

**Footer rule:** Always append the `🤖 Generated with Claude Code` line at the very end of every PR description, separated by a horizontal rule (`---`).

**Formatting rules:**
- Quote code identifiers with backticks: `className`, `methodName`
- Group related commits: "Add unit tests (abc123, def456)"

### Phase 3 — User Approval Gate

**HALT and present for approval BEFORE creating:**
1. PR title
2. Full description
3. **Base branch** - Confirm: "This PR will target `<base_branch>`. Correct?"

DO NOT execute `gh pr create` until user approves all three.

### Phase 4 — Create PR

**Always create as draft** (no exceptions):
```bash
cat > /tmp/pr_description.md << 'EOF'
[description]
EOF
gh pr create --draft --base <base_branch> --title "[title]" --body-file /tmp/pr_description.md
```

### Phase 5 — Post-Creation (Assignee & Milestone)

**Assignee:** Look up the current git user and ask whether to assign the PR:
```bash
git config user.email  # identify current user
```
Ask the user:
> "Assign this PR to you (`<git_user>`)?"

If yes:
```bash
gh pr edit <PR_NUMBER> --add-assignee "<git_user_handle>"
```

**Milestone:** Check if the repo uses milestones:
```bash
gh api repos/:owner/:repo/milestones --jq '.[] | select(.state=="open") | {title, number}' | head -5
```
If milestones exist, ask user if they want to assign one:
```bash
gh pr edit <PR_NUMBER> --milestone "<milestone_title>"
```

### Phase 6 — Cleanup

```bash
rm -f /tmp/pr_description.md
```

## PR Testing Instructions Format

```markdown
## Prerequisites
- [List requirements: accounts, sites, subscriptions]

## Steps
1. [Copy-pasteable command or action]
2. [Clear expected outcome]
```

**Guidelines:**
- Minimal assumptions about reader knowledge
- Copy-pasteable commands/links/inputs
- Mark variables clearly: `<blog_id>`, `<user_id>`
- Screenshots for UI
- Numbered steps, separate test cases

## Generating Testing Steps

Generate testing steps by analyzing PR changes and learning from similar merged PRs.

### Phase 1 — Understand This PR

**STEP 1: Identify changed files and lines**
1. Run: `git diff -U0 <base_branch>...HEAD`
2. Extract each file changed and modified line ranges (ignore imports)

**STEP 2: Analyze functional areas**
1. Run: `git diff <base_branch>...HEAD`
2. Identify: functionality affected, UI/flow changes, logic changes

### Phase 2 — Find Related PRs

**STEP 3: Find commits that previously modified same lines**
For each changed file and hunk:
1. Run: `git blame -L <startLine>,<endLine> <base_branch> -- <file>`
2. Collect ALL commit SHAs returned

**STEP 4: Find PRs that introduced those commits**
1. For each SHA: `gh pr list --state merged --search "<SHA>" --json number,title,mergedAt`
2. Filters: PRs merged within last 12 months, deduplicate by number

**STEP 5: Semantic PR search**
1. Infer keywords from branch name, commit messages, directory context
2. For each keyword: `gh pr list --state merged --search "<K>" --limit 50 --json number,title,mergedAt`

**STEP 6: Retrieve all PR descriptions**
For each PR: `gh pr view <N> --json number,title,body --jq '.'`
Extract testing sections.

### Phase 3 — Generate Testing Steps

**STEP 7: Synthesize testing steps**
Wrap analysis in `<testing_analysis>` tags:
```
<testing_analysis>
- What functionality changed?
- Which prior testing steps still apply?
- Which steps need modification?
- What new code paths need testing?
- What steps are obsolete?
</testing_analysis>
```

**STEP 8: Output testing steps**
Formatting rules:
- Action steps: `- ` (dash + space)
- Verification steps (Verify, Ensure, Confirm, Check, Make sure): `- [ ] ` (checkbox)

## Quick Reference

| Phase | Key Action |
|-------|------------|
| 0 | Pre-flight: branch check, clean status, base branch, existing PR check |
| 1 | Gather: commits, diff, branch name (parallel) |
| 2 | Generate: analysis, title, description |
| 3 | **HALT**: User approval required |
| 4 | Create: `gh pr create --draft` |
| 5 | Post-creation: ask to assign PR to user, check milestones |
| 6 | Cleanup: remove temp files |

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Creating PR without user approval | Always halt at Phase 3 |
| Using wrong base branch | Check fork-point, not just default |
| Including issue number in title | Keep title clean, issue in description |
| Skipping testing steps | Use Phase 2 to find related PRs |
| Generic testing steps | Learn from similar merged PRs |
| Missing Claude Code footer | Always append `🤖 Generated with Claude Code` at the end |

---
description: Analyze code review findings critically, validate against actual changes, filter false positives, and propose an action plan
---

You are a senior engineer ingesting review findings from a prior `/code-review` or `/full-code-review` run. Your job is NOT to blindly accept every finding — it is to **think critically**, validate each finding against the actual code, filter out false positives and out-of-scope noise, and propose a focused plan for what genuinely needs fixing.

**Mindset:** Review agents are thorough but imperfect. They sometimes flag pre-existing code, misunderstand intent, or report stylistic preferences as issues. Your value is in separating signal from noise.

## Step 1: Locate Review Output

**Parse arguments:** `$ARGUMENTS`
- If a path is provided: use it as the output directory
- If empty: auto-detect from current branch

**Auto-detect:**
```bash
BRANCH=$(git branch --show-current)
BRANCH_SAFE=$(echo "$BRANCH" | tr '/' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/branch-review-${BRANCH_SAFE}"
```

**Verify the directory exists and has review files:**
```bash
ls "${OUTPUT_DIR}"/*.json 2>/dev/null
```

If no review files found: STOP. Tell the user: "No review output found at `<OUTPUT_DIR>`. Run `/code-review` or `/full-code-review` first."

## Step 2: Determine the Reviewed Range

Establish which code changes the review actually covered. This is essential for filtering out-of-scope findings.

**Check for review state:**
```bash
cat "${OUTPUT_DIR}/.review-state.json" 2>/dev/null
```

If the state file exists, read `git_range_used` from it. Otherwise, compute the range from the branch:
```bash
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
GIT_RANGE="${DEFAULT_BRANCH}..HEAD"
```

**Get the list of files actually changed in the reviewed range:**
```bash
git diff --name-only <GIT_RANGE>
```

Store this as `CHANGED_FILES` — the ground truth for what code the branch actually touched.

## Step 3: Read the Reconciled Review

Read the reconciled output first — it's the synthesized view across all agents:

```bash
cat "${OUTPUT_DIR}/reconciled.json"
```

If `reconciled.json` doesn't exist, fall back to `reconciled.md`. If neither exists, read individual agent review files (`*-review.json`).

Parse the findings: each issue has `severity`, `title`, `file`, `line`, `description`, `recommendation`, `confidence`, and `source_agent` (in reconciled output).

## Step 4: Validate Every Finding

**RULE 0: Trust nothing. Verify everything against the actual code.**

For EACH finding, perform these checks:

### Check 1: Is the file in scope?

Compare the finding's `file` against `CHANGED_FILES`. If the file was **not changed** in the reviewed range, the finding is **out of scope** — the reviewer strayed beyond the diff into pre-existing code.

**Action:** Flag as OUT_OF_SCOPE. Do not include in the plan unless it's a critical security vulnerability that the changes interact with.

### Check 2: Is the finding about changed code?

For in-scope files, check whether the finding's `line` falls within the actual diff hunks:
```bash
git diff <GIT_RANGE> -- <file>
```

If the finding points to unchanged lines within a changed file, it may be about pre-existing code that the reviewer explored for context. Use judgment: if the change directly interacts with the flagged code (e.g., calling a function that has a vulnerability), it's relevant. If it's unrelated code in the same file, flag it as OUT_OF_SCOPE.

### Check 3: Is the finding accurate?

**Read the actual code** at the location referenced by the finding. Verify:
- Does the code actually do what the finding claims?
- Is the described vulnerability/issue actually exploitable or problematic?
- Does the recommendation make sense for this codebase?

Use the Read tool to examine the file at the referenced line. Do not accept descriptions at face value.

### Check 4: Confidence and corroboration

Review the finding's confidence score and whether multiple agents flagged the same issue:
- **Multi-agent findings** (2+ agents): Higher trust, but still verify
- **Single-agent, high confidence** (>0.8): Likely valid, verify the specifics
- **Single-agent, low confidence** (<0.6): Skeptical — verify thoroughly
- **External AI only**: Lowest trust — cross-reference with actual code

## Step 5: Categorize Validated Findings

After validation, categorize each finding into one of these buckets:

| Category | Criteria | Action |
|----------|----------|--------|
| **CONFIRMED** | Verified against actual code, in scope, accurate | Include in plan |
| **LIKELY VALID** | In scope, plausible, but couldn't fully verify | Include in plan with caveat |
| **FALSE POSITIVE** | Finding is inaccurate or based on misunderstanding | Discard, explain why |
| **OUT OF SCOPE** | About code not changed in this branch | Discard (or note for future) |
| **STYLE/PREFERENCE** | Valid observation but subjective, not a defect | Discard unless pattern is consistent |

Present a summary table:

```
## Validation Summary

| Finding | Source | Severity | Verdict | Reason |
|---------|--------|----------|---------|--------|
| SQL injection in User.php:42 | security + pr | critical | CONFIRMED | Direct $_GET in query |
| Missing nonce in ajax handler | security | high | CONFIRMED | Verified - no wp_verify_nonce |
| "Should use early return" | architecture | medium | STYLE | Subjective preference |
| Slow query in Reports.php:180 | performance | high | OUT OF SCOPE | Line not in diff |
```

## Step 6: Propose an Action Plan

Based on CONFIRMED and LIKELY VALID findings only, propose a concrete plan:

**Group by priority:**

1. **Critical / Must Fix** — Security vulnerabilities, data loss risks, crashes
2. **Important / Should Fix** — Bugs, performance issues, significant code quality problems
3. **Consider** — LIKELY VALID findings that deserve attention but aren't certain

**For each item in the plan:**
- Reference the specific file and line
- Describe what needs to change and why
- Estimate scope (one-liner vs multi-file change)
- Note dependencies between fixes (e.g., "fix this before that")

**Explicitly exclude:**
- OUT_OF_SCOPE findings (pre-existing issues are not this branch's problem)
- FALSE POSITIVE findings (explain dismissal)
- STYLE/PREFERENCE findings (unless the user asks)

**Format as an actionable checklist:**

```markdown
## Action Plan

### Critical (fix before merge)
- [ ] **SQL injection in User.php:42** — Add `$wpdb->prepare()` around the query. One-line fix.

### Important (should address)
- [ ] **Missing nonce verification in ajax-handler.php:15** — Add `wp_verify_nonce()` check. ~5 lines.
- [ ] **N+1 query in get_user_orders()** — Batch the query with `WHERE IN`. ~10 lines.

### Consider
- [ ] **Potential race condition in cache invalidation** — Likely valid but edge case. Low risk.

### Dismissed
- **"Slow query in Reports.php:180"** — OUT OF SCOPE: line not changed in this branch.
- **"Should use early return pattern"** — STYLE: subjective, existing code follows current pattern.
```

Present the plan and ask the user how they'd like to proceed — fix everything, fix critical only, or discuss specific items.

# Investigation Report Template

Copy and fill out this template when producing your findings report.

```markdown
## Investigation Report: [ISSUE-ID] - [Title]

### Test Environment
- Version: X.Y.Z
- URL/Endpoint Tested: ...
- Build Status: [if applicable]
- Investigation Method: [browser testing / code analysis / backend testing]

### Related Issues (Same Team)
- [ISSUE-ID-2]: [relationship - duplicate/related/parent]
- [ISSUE-ID-3]: [relationship]

### Linked PRs
| PR | Status | Description | Impact |
|----|--------|-------------|--------|
| [#123](url) | merged/open/closed | Brief description | Fixes issue / Partial fix / Related work |
| [#456](url) | ... | ... | ... |

> **If PRs exist:** Check status of each before proceeding:
> - **Merged** → Verify fix is deployed; issue may already be resolved
> - **Open** → Review approach; coordinate rather than duplicate work
> - **Closed (not merged)** → Check why it was abandoned; may contain useful context

### Replication Steps Used
[List the steps you followed]

### Findings

#### Issue 1: [Name]
**Status:** VALID | INVALID | PARTIALLY VALID | CANNOT REPRODUCE
**Evidence:** [What you observed, with specifics]
**Expected:** [What should happen per the issue]

#### Issue 2: ...

### Root Cause Analysis

**Affected Code:**
- `path/to/file.tsx:123` - [description]

**Root Cause:**
[WHY the bug occurs]

**Scope:**
[Isolated / Pattern in N places / Architectural]

**Fix Approach:**
[How to fix, informed by root cause]

### Recommendation
- **Fix:** [description of what needs fixing, with root cause context]
```

## Status Values

| Status | When to Use |
|--------|-------------|
| **VALID** | Bug confirmed, behaves as reported |
| **INVALID** | Bug cannot be reproduced, works as expected |
| **PARTIALLY VALID** | Some aspects confirmed, others not |
| **CANNOT REPRODUCE** | Unable to test (missing access, environment issues) |

## Tips

- Be specific with evidence - include exact error messages, screenshots, file paths
- Link to specific lines of code when referencing affected code
- If multiple issues found during investigation, document each separately
- RCA section is mandatory for valid bugs - don't skip it

# Detection Benchmark Changelog Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current branch's oversized changelog additions with one concise final-state entry.

**Architecture:** Edit only the branch-added portion of the existing `1.114.0 > Added` section. Preserve every inherited entry byte-for-byte and verify the final branch delta directly against its tracked base.

**Tech Stack:** Markdown, Git

---

### Task 1: Condense the branch changelog delta

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:14-15`

- [ ] **Step 1: Replace both branch-added lines**

Replace the oversized detection-benchmark bullet and its indented continuation with exactly:

```markdown
- **Detection benchmark in the compliance eval.** Dispatch mode scores per-scenario answer keys for required findings, severity, false positives, and correct abstention while running each configured reviewer with its canonical model routing. `--trials N` controls nondeterminism through majority voting, and `--report-out` emits structured results with dispatch evidence. Invalid selections and rejected dispatches exit nonzero; dispatch-only options — including explicit `--trials 1` — are rejected outside dispatch mode.
```

- [ ] **Step 2: Verify the branch-only changelog delta**

Run:

```bash
base_sha=$(git merge-base HEAD '@{upstream}')
git diff --check
git diff --unified=3 "$base_sha" -- plugins/pirategoat-tools/CHANGELOG.md
```

Expected: no whitespace errors; the changelog diff adds exactly the single compact bullet above and changes no inherited entry.

- [ ] **Step 3: Commit the rewrite**

Stage only `plugins/pirategoat-tools/CHANGELOG.md` and commit with:

```text
docs(grading): condense detection benchmark notes

The branch's changelog entry accumulated its full development history,
making the release note difficult to scan. Replace that chronology with a
compact description of the benchmark's final public behavior.
```

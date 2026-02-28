# Reviewer Precision Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce noise in reviewer agent output by addressing the three largest precision gaps identified in the ingest validation analysis: STYLE/PREFERENCE (15.7%), OUT OF SCOPE (6.4%), and false positives (4.8%), plus fix UNKNOWN attribution (25.6%) to enable future measurement.

**Architecture:** Seven independent changes across three layers — shared protocol (affects all agents), individual agent files (targeted fixes), and the ingest pipeline (downstream attribution). Changes are additive guardrails, not behavioral rewrites. Each task is independently committable and testable.

**Tech Stack:** Markdown (agent definitions), Python (ingest script, test infrastructure), pytest (deterministic tests)

**Data source:** `.claude/docs/analysis/2026-02-28-ingest-validation-analysis.md` — 313 validated findings across 29 sessions, 2 projects (Go CLI + WordPress/React).

---

## Task 1: wp-architecture-reviewer — Add framework convention anti-FP rules

**Rationale:** Highest per-agent FP rate at 13% (3/23 findings). All 3 FPs share the same root cause: misunderstanding documented framework APIs. This is the most surgical, highest-confidence fix.

**Data:** FP #5 (hook reference flagged — it's a documented `useIsComplete` API), FP #8 (`useIsWooPaymentsActivated` returns `undefined` — correct per `CompletionState` type), FP #9 (hardcoded Stripe SDK errors flagged for i18n — they're developer-only).

**Files:**
- Modify: `plugins/pirategoat-tools/agents/wp-architecture-reviewer.md:54-71`

**Step 1: Add anti-FP rules to the agent**

After the "Pragmatic Hooks Principle" block (line 71), add a new subsection:

```markdown
**Anti-False-Positive Checks:**

Before reporting a finding, verify it doesn't fall into these known FP patterns:

1. **Framework API misidentification:** Before flagging a pattern as an API bypass, tight coupling, or architectural violation, check if it's a documented framework API by reading the relevant type definitions or interface files. If a type/interface explicitly declares the pattern (e.g., `CompletionState = boolean | undefined`), it's intentional — drop the finding.

2. **Developer-only strings don't need i18n:** Error messages from SDK integrations (Stripe, payment gateways), debug assertions, and errors that never surface in the user-facing UI do NOT need internationalization. Only flag i18n for strings rendered to end users.

3. **Clean removals are not dead code:** Code that was intentionally removed in the PR is the PR doing its job, not a "dead code" finding. Only flag dead code that the PR *introduces* or *leaves behind*.
```

**Step 2: Run existing tests to verify no regressions**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS (agent config unchanged, these are content-only changes)

**Step 3: Commit**

```
feat(pirategoat-tools): add anti-FP checks to wp-architecture-reviewer

The ingest validation analysis (313 findings, 29 sessions) found
wp-architecture-reviewer has the highest FP rate at 13% (3/23).
All three FPs share one root cause: misunderstanding documented
framework conventions — flagging typed APIs as problems, developer
errors as needing i18n, and clean removals as dead code.

Add three explicit anti-FP checks that address each documented
failure mode.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 2: Shared protocol — Add "Bug or Preference?" self-check gate

**Rationale:** STYLE/PREFERENCE is the single largest noise category at 15.7% (49/313 findings). Most concentrates at LOW severity (which is only 57% actionable). A self-check question before reporting LOW/MEDIUM findings can filter subjective opinions without losing real issues.

**Data:** 49 STYLE findings across all agents. patterns-reviewer (14), architecture-reviewer (12), history-insights (7), performance-reviewer (6) are the biggest contributors.

**Files:**
- Modify: `plugins/pirategoat-tools/agents/shared/reviewer-protocol.md:92-121`

**Step 1: Add the self-check gate to the protocol**

After the existing 4-point verification checklist (line 109, after "Am I reviewing the change, or the codebase?"), add a 5th point:

```markdown
5. **Is this a bug or a preference?** For LOW and MEDIUM findings: if this is a formatting choice, naming opinion, code organization style, or "I would have done it differently" without a concrete defect, regression, or security concern — it's a preference. Drop it.
```

**Step 2: Run existing tests to verify no regressions**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS

**Step 3: Commit**

```
feat(pirategoat-tools): add bug-or-preference self-check to reviewer protocol

The ingest validation analysis shows 15.7% of all findings are
STYLE/PREFERENCE — the largest noise category. This noise
concentrates at LOW severity (57% actionable vs 93% for HIGH).

Add a 5th verification question to the shared protocol requiring
agents to distinguish bugs from preferences before reporting
LOW/MEDIUM findings. Preferences without concrete defects get
dropped.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 3: Shared protocol — Add code verification mandate for factual claims

**Rationale:** 7/15 FPs (47%) — the #1 FP root cause — are agents making factual claims about code without reading the actual implementation. Examples: claiming `f.Close()` is missing (it's on line 46), claiming `rel=noopener` is absent (it IS in `NOTE_ALLOWED_ATTR`).

**Data:** FP #1 (FD leak claim — close is unconditional), FP #3 (`target="_blank"` without `rel=noopener` — `rel` IS in allowlist), FP #2 (O(N^2) claim — already linear), FP #3/#5 (UTF-8 claims — already uses rune iteration).

**Files:**
- Modify: `plugins/pirategoat-tools/agents/shared/reviewer-protocol.md:92-121`

**Step 1: Add the verification mandate**

After the new point 5 from Task 2, add a 6th point:

```markdown
6. **Did I verify my factual claim?** If your finding says code does or doesn't do something specific (missing close, missing attribute, missing null check, O(N^2) complexity), you MUST read the actual implementation lines with the Read tool to confirm. Do not infer behavior from context or variable names. 47% of false positives come from factual claims that don't match the actual code.
```

**Step 2: Run existing tests**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS

**Step 3: Commit**

```
feat(pirategoat-tools): add factual-claim verification to reviewer protocol

7 of 15 false positives (47%) in the ingest validation analysis
stem from agents making factual claims about code without reading
the actual implementation — e.g., claiming a close is missing when
it's present, or claiming an attribute is absent when it's in the
allowlist.

Add a 6th verification point requiring agents to use the Read tool
to confirm any factual claim about what code does or doesn't do
before reporting.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 4: Shared protocol — Strengthen scope enforcement with STOP pattern

**Rationale:** 20 OOS findings (6.4% overall, 8.8% in WordPress) despite existing scope rules. The current wording ("If you are about to report a finding, STOP") is passive. A STOP escalation pattern — proven in the a11y-reviewer (v1.35.2) — makes the check procedural and harder to skip.

**Data:** patterns-reviewer contributes 9 OOS, pr-reviewer 4, history-insights 4. WordPress codebases have 7pp more OOS due to interconnected state.

**Files:**
- Modify: `plugins/pirategoat-tools/agents/shared/reviewer-protocol.md:92-121`

**Step 1: Add STOP escalation before add_issue()**

Replace the current passive paragraph on line 103:

```
If you are about to report a finding, STOP. Verify it is in changed code (the diff), not explored code. Findings on unchanged code are false positives — drop them.
```

With:

```markdown
**STOP CHECK — before every `add_issue()` call:**

State the file path and line number for this finding. Then answer two questions:
1. Is this file in `CHANGED_FILES`? (If NO → drop: not in diff)
2. Is this line in a diff hunk? (If NO → drop: pre-existing code)

If either answer is NO, this is exploration context, not a finding. Do NOT call `add_issue()`. This check is mandatory — findings on unchanged code are false positives.
```

**Step 2: Run existing tests**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS

**Step 3: Commit**

```
feat(pirategoat-tools): add STOP escalation for scope enforcement

20 OOS findings (6.4%) get through despite existing scope rules.
The current instruction is passive ("STOP. Verify it is in changed
code"). Replace with a procedural STOP CHECK that requires stating
the file and line, then answering two yes/no questions before
every add_issue() call.

This mirrors the STOP escalation pattern proven effective in the
a11y-reviewer (v1.35.2).

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 5: architecture-reviewer — Add WordPress context dampener

**Rationale:** Precision drops from 80% (Go) to 53.6% (WordPress). The noise is abstract SOLID opinions and FPs from misunderstanding WordPress conventions (0 FPs in Go, 3 in WordPress). A conditional confidence reduction for abstract opinions in WordPress code steers the agent toward concrete issues.

**Data:** 12 STYLE findings in architecture-reviewer (most in ciab-admin), 3 FPs all in WordPress. Zero FPs in Go.

**Files:**
- Modify: `plugins/pirategoat-tools/agents/architecture-reviewer.md:122-145`

**Step 1: Add WordPress context dampener**

After the "Pattern Warning Signs" section (line 145), add:

```markdown
### WordPress/PHP Plugin Context

When reviewing WordPress plugin or PHP theme code, apply these adjustments:

- **Abstract architecture opinions without concrete impact get -10 confidence.** Claims like "this violates SRP" or "consider introducing an interface" must cite a specific bug, regression, or maintainability hazard in the current code. WordPress plugins prioritize convention-over-architecture — structural purity opinions without concrete defects are STYLE, not findings.
- **Defer WordPress-specific concerns** to wp-architecture-reviewer. Do not duplicate hook design, WPCS, i18n, or backwards compatibility analysis.
- **Verify framework conventions before flagging.** WordPress and WooCommerce use patterns (global state, hook-based architecture, service containers) that may look like anti-patterns to a general architecture reviewer but are intentional framework conventions.
```

**Step 2: Run existing tests**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS

**Step 3: Commit**

```
feat(pirategoat-tools): add WordPress context dampener to architecture-reviewer

Architecture-reviewer precision drops from 80% (Go) to 53.6%
(WordPress). The gap is abstract SOLID opinions that don't apply
to WordPress conventions (12 STYLE) and framework convention
misunderstandings (3 FPs, all in WordPress, 0 in Go).

Add conditional confidence adjustment: abstract architecture
opinions in WordPress code without concrete defects get -10
confidence, pushing weak findings below the report threshold.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 6: history-insights-reviewer — Tighten relevance enforcement

**Rationale:** 67.3% precision overall, drops to 60% in WordPress (vs 100% in Go). The agent surfaces interesting-but-irrelevant historical context from unrelated code areas. A relevance gate ties insights to the specific code being changed.

**Data:** 7 STYLE + 4 OOS findings. Precision drop from 100% to 60% between projects suggests WordPress's broader interconnected codebase tempts the agent into tangential history mining.

**Files:**
- Modify: `plugins/pirategoat-tools/agents/history-insights-reviewer.md:192-202`

**Step 1: Add relevance gate to constraints**

After the existing "Stay scenario-focused" constraint (line 194), add a new constraint:

```markdown
**Tie insights to changed code:** Every insight must connect to code being CHANGED in this PR. An insight about how the team handled caching in module X is not relevant to a PR changing authentication in module Y, even if both are interesting. Before reporting, ask: "Would the PR author need this specific precedent to avoid a concrete mistake in THIS code?" If the answer is "it's just good to know" — classify as LEARN (INFO severity) or drop it entirely.
```

**Step 2: Run existing tests**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests PASS

**Step 3: Commit**

```
feat(pirategoat-tools): tighten relevance enforcement in history-insights-reviewer

History-insights precision drops from 100% (Go) to 60% (WordPress)
due to tangential insights from unrelated code areas. The broader
WordPress codebase tempts the agent into interesting-but-irrelevant
history mining.

Add a relevance gate: every insight must connect to code being
changed in the PR. "Good to know" insights get INFO severity or
are dropped.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 7: ingest-code-review — Fix UNKNOWN source attribution

**Rationale:** 80 findings (25.6%) have no agent source attribution. This blinds per-agent analysis and prevents measuring whether Tasks 1-6 actually work. Each review output file is named `{agent-name}-review.json`, so the source is recoverable.

**Data:** "UNKNOWN (no source)" is the largest single row in the per-agent table with 80 findings and 8.8% FP rate. Without attribution, we can't assign those FPs to the agents that produced them.

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/ingest-code-review.py:83-115` (step 2 guidance)
- Test: `plugins/pirategoat-tools/tests/test_commands.py` (existing structural tests)

**Step 1: Add source inference instruction to step 2**

In the `get_step_guidance` function, step 2 (lines 83-115), find the `EXTRACT per finding` block and modify it. Current text:

```python
                "EXTRACT per finding:",
                "  - file       (path to the file containing the issue)",
                "  - line       (line number)",
                "  - severity   (critical/high/medium/low)",
                "  - title      (short description)",
                "  - source_agents (list of agents that flagged this)",
                "  - confidence (0.0–1.0)",
```

Replace with:

```python
                "EXTRACT per finding:",
                "  - file       (path to the file containing the issue)",
                "  - line       (line number)",
                "  - severity   (critical/high/medium/low)",
                "  - title      (short description)",
                "  - source_agents (list of agents that flagged this — see inference rule below)",
                "  - confidence (0.0–1.0)",
                "",
                "SOURCE INFERENCE RULE:",
                "  If a finding lacks an explicit source/agent field, infer it from the filename:",
                "  - {agent-name}-review.json → source_agents = [\"{agent-name}\"]",
                "  - reconciled.json correlated findings → source_agents = union of contributing agents",
                "  Every finding MUST have a non-empty source_agents list. 'UNKNOWN' is not acceptable.",
```

**Step 2: Run existing tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All tests PASS (step content is guidance text, not structural)

**Step 3: Commit**

```
feat(pirategoat-tools): add source inference rule to ingest-code-review

25.6% of validated findings (80/313) have no agent source
attribution, preventing per-agent precision tracking. Each review
output file is already named {agent-name}-review.json, making the
source recoverable.

Add a SOURCE INFERENCE RULE to step 2 of the ingest workflow
requiring the agent name to be inferred from the filename when no
explicit source field is present. This is a force multiplier —
it makes all other precision improvements measurable.

Refs .claude/docs/analysis/2026-02-28-ingest-validation-analysis.md
```

---

## Task 8: Version bump and changelog

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`
- Modify: `.claude-plugin/marketplace.json`

**Step 1: Update CHANGELOG.md**

Add a new version section at the top (after the `## [1.36.0]` entry):

```markdown
## [1.37.0] - 2026-02-28

### Added

- **reviewer-protocol — Three precision guardrails from ingest validation analysis (313 findings, 29 sessions)** — (1) "Bug or Preference?" self-check gate for LOW/MEDIUM findings to reduce STYLE/PREFERENCE noise (15.7% of output); (2) Factual-claim verification mandate requiring Read tool confirmation before reporting what code does/doesn't do (addresses 47% of false positives); (3) STOP escalation pattern before every `add_issue()` call requiring file+line scope verification (addresses 6.4% OUT OF SCOPE rate). All three changes are additive to the existing 4-point verification checklist.
- **wp-architecture-reviewer — Anti-FP checks for framework conventions** — Three rules addressing the agent's 13% FP rate: verify against type definitions before flagging APIs, developer-only strings don't need i18n, and clean removals are not dead code.
- **architecture-reviewer — WordPress context dampener** — Conditional -10 confidence for abstract SOLID opinions in WordPress code without concrete defects, addressing the precision drop from 80% (Go) to 53.6% (WordPress).
- **history-insights-reviewer — Relevance gate** — Insights must connect to code being changed in the PR; "good to know" findings from unrelated areas get INFO severity or are dropped.
- **ingest-code-review — Source inference rule** — Step 2 now requires inferring agent source from filename when no explicit field is present, eliminating the 25.6% UNKNOWN attribution gap.
```

**Step 2: Bump version in marketplace.json**

Update the pirategoat-tools version from current to `1.37.0`.

**Step 3: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```
chore(pirategoat-tools): bump to v1.37.0

Seven precision improvements from the ingest validation analysis
(313 findings, 29 sessions, 2 projects). See CHANGELOG.md for
full details.
```

---

## Execution Notes

**Task independence:** Tasks 1-7 are independent of each other and can be executed in any order. Task 8 depends on all others being complete.

**Testing strategy:** All changes are to markdown content or Python string guidance — no structural changes to scripts or agent configs. The existing `test_bootstrap_reviewer.py` and `test_commands.py` test suites verify structural integrity. Content effectiveness will be measured in subsequent ingest validation sessions.

**Risk mitigation:** Every change is additive (new paragraphs/rules added to existing sections). No existing text is removed except the single passive sentence in Task 4 which is replaced by a stronger version of the same instruction. If any change causes unexpected agent behavior, it can be reverted independently.

**Measurement plan:** After deploying, run 5-10 review sessions and re-run the ingest validation analysis. Compare per-agent precision rates against the baselines in `.claude/docs/analysis/2026-02-28-ingest-validation-analysis.md`. Target: overall actionable rate from 69.3% to 78%+, wp-architecture FP rate from 13% to <5%, UNKNOWN attribution from 25.6% to <5%.

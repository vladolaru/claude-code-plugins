# Adaptive Agent Dispatch Design

**Date:** 2026-02-28
**Status:** Design approved — ready for implementation

---

## Problem

The review pipeline dispatches agents based on file-type matching only. The preflight step (`review-scope.py --preflight`) checks whether a domain has files in the diff — if PHP files exist, security-reviewer runs. This is insufficient:

- **security-reviewer** passes the file-type gate in 81% of sessions but only produces findings in 11%
- **dead-code-reviewer** passes in 40% but only produces findings in 32%
- **wp-architecture-reviewer** is dispatched 55 times across 47 sessions but only hits in 33%

The result: wasted token budget and pipeline time on agents unlikely to find anything.

**Data source:** 47 ciab-admin sessions, 305 agent executions — see `.claude/docs/analysis/2026-02-28-reviewer-agent-value-ranking.md`

---

## Solution: LLM-Assisted Triage (Step 3.6)

Add an LLM triage step between the existing preflight file-type gate and agent dispatch. The orchestrator (already an LLM) reads the diffstat and commit messages, then evaluates conditional agents against per-agent dispatch criteria.

### Agent Classification

| Category | Agents | Gate |
|---|---|---|
| **Always dispatch** | pr-reviewer, patterns-reviewer, history-insights-reviewer | Preflight file-type only |
| **File-type gated** | js-tests, php-tests, e2e-tests, go-tests | Preflight file-type only (existing, works well) |
| **LLM-triaged** | security, dead-code, architecture, wp-architecture, performance, a11y | Preflight file-type → LLM triage |
| **On-demand only** | gemini, codex | Never auto-dispatched |

### Flow Change

```
Step 3.5: Preflight scope check (unchanged — fast Python file-type gate)
Step 3.6: Adaptive agent triage (NEW — LLM evaluates 6 conditional agents)
  Input:  diffstat, commit messages, list of conditional agents that passed preflight
  Output: DISPATCH/SKIP decision per agent with reasoning
  Rule:   when in doubt, dispatch
Step 4:   Dispatch agents (only those that passed both gates)
```

### Safety Mechanisms

1. **"When in doubt, dispatch" default** — the rubric explicitly says to only skip when confident none of the criteria apply. Accepts some wasted dispatches to avoid missing findings.
2. **Mandatory skip logging** — every skip decision must include reasoning. Creates an audit trail for retrospective validation.

---

## Per-Agent Dispatch Criteria

### security-reviewer

**DISPATCH WHEN** the PR expands or modifies attack surface:
- New or modified endpoints that accept external input (any framework's routing/handler registration)
- Code that processes user-supplied data (form fields, query parameters, request bodies, file uploads)
- Database operations (reads, writes, raw queries)
- Dynamic content rendered to HTML or other output formats
- Authentication, authorization, or session management changes
- File system operations with user-influenced paths
- Third-party API integrations or webhook handlers
- Cryptographic operations or secret/token handling
- Commits introducing new entry points, handlers, or data processing paths

**Design note:** Dispatch is based on attack surface exposure, not on the presence of security code. The reviewer's job is to verify security measures exist — so dispatch when the PR touches anything an external actor could reach, regardless of whether protections are already in place.

### dead-code-reviewer

**DISPATCH WHEN** the PR changes the dependency graph:
- Files deleted or renamed in the diff
- Significant code removal (lines removed > lines added by meaningful margin)
- Refactoring commits (commits mentioning refactor, extract, move, rename, consolidate, remove, delete)
- Import/require statements added or removed
- New files that replace or supersede existing ones

### architecture-reviewer

**DISPATCH WHEN** the PR introduces structural changes:
- New classes, interfaces, or abstract types added
- Files spanning 3+ architectural layers (e.g., controller + service + repository in one PR)
- Commits mentioning architecture, pattern, refactor, restructure, decouple, extract
- Large PRs (20+ files or 500+ lines changed) that suggest structural reorganization
- New modules or packages introduced

### wp-architecture-reviewer

**DISPATCH WHEN** the PR touches WordPress integration points:
- PHP files using WordPress APIs (hooks, filters, options, transients, REST registration)
- WooCommerce-specific files (payment gateways, admin pages, checkout)
- Files registering or modifying admin menus, settings pages, or custom post types
- Commits mentioning hooks, filters, backwards compatibility, deprecation, i18n
- Plugin bootstrap or activation/deactivation files

### performance-reviewer

**DISPATCH WHEN** the PR touches data flow or rendering:
- Database query files, API calls, data fetching hooks (useEffect, useQuery, useSWR)
- Files handling lists, tables, pagination, or bulk operations
- Asset loading (CSS/JS enqueue, lazy loading, code splitting)
- Caching logic (transients, object cache, memoization)
- Commits mentioning performance, speed, optimize, cache, query, N+1, load time

### a11y-reviewer

**DISPATCH WHEN** the PR modifies user-facing UI:
- JSX/TSX component files with interactive elements (buttons, forms, modals, dropdowns)
- ARIA attribute changes or focus management code
- New UI components or significant visual changes
- CSS/SCSS changes that affect visibility, focus indicators, or contrast
- Commits mentioning accessibility, a11y, keyboard, screen reader, focus, ARIA

---

## Triage Prompt (Step 3.6)

This block goes into the orchestrator command (`full-code-review.md` and `code-review.md`):

```markdown
## Step 3.6: Adaptive Agent Triage

You have the diffstat and commit messages from Steps 1-3.
Evaluate each conditional agent below. For each one:

1. Check its DISPATCH WHEN criteria against the diffstat and commit messages
2. Decide: DISPATCH or SKIP
3. Log your reasoning (required for every decision)

**DEFAULT: When in doubt, DISPATCH.** Only skip when you are confident
none of the criteria apply.

### Conditional Agents

**security-reviewer**
DISPATCH WHEN:
- New or modified endpoints accepting external input
- Code processing user-supplied data
- Database operations
- Dynamic content rendered to output
- Auth, authorization, or session management changes
- File system operations with user-influenced paths
- Third-party API or webhook integrations
- Cryptographic or secret/token handling
- Commits introducing new entry points or data processing

**dead-code-reviewer**
DISPATCH WHEN:
- Files deleted or renamed
- Significant code removal (removed > added)
- Refactoring commits (extract, move, rename, consolidate, remove, delete)
- Import/require statements added or removed
- New files replacing or superseding existing ones

**architecture-reviewer**
DISPATCH WHEN:
- New classes, interfaces, or abstract types added
- Files spanning 3+ architectural layers
- Commits mentioning architecture, refactor, restructure, decouple, extract
- Large PRs (20+ files or 500+ lines)
- New modules or packages introduced

**wp-architecture-reviewer**
DISPATCH WHEN:
- PHP files using WordPress APIs (hooks, filters, options, transients, REST)
- WooCommerce-specific files
- Admin menus, settings pages, or custom post types
- Commits mentioning hooks, filters, backwards compatibility, deprecation, i18n
- Plugin bootstrap or activation/deactivation files

**performance-reviewer**
DISPATCH WHEN:
- Database queries, API calls, data fetching hooks
- Lists, tables, pagination, bulk operations
- Asset loading, lazy loading, code splitting
- Caching logic (transients, object cache, memoization)
- Commits mentioning performance, optimize, cache, query, load time

**a11y-reviewer**
DISPATCH WHEN:
- JSX/TSX with interactive elements (buttons, forms, modals, dropdowns)
- ARIA attributes or focus management code
- New UI components or significant visual changes
- CSS/SCSS affecting visibility, focus indicators, or contrast
- Commits mentioning accessibility, a11y, keyboard, screen reader, ARIA

### Triage Output

For each conditional agent, log:

  TRIAGE: <agent-name>: <DISPATCH|SKIP> — <one-line reasoning>

Example:
  TRIAGE: security-reviewer: DISPATCH — PR adds new REST endpoint in src/api/users.ts
  TRIAGE: dead-code-reviewer: SKIP — no files deleted, no refactoring commits, net +120 lines
  TRIAGE: architecture-reviewer: SKIP — single component file changed, no structural reorganization
  TRIAGE: wp-architecture-reviewer: DISPATCH — PHP files modify WooCommerce payment gateway hooks
  TRIAGE: performance-reviewer: DISPATCH — new useQuery hook in data-fetching layer
  TRIAGE: a11y-reviewer: DISPATCH — new modal component with form inputs

Agents skipped by triage are recorded in the reconciliator context:
  <agent>: STATUS=SKIPPED_TRIAGE (<one-line reason>)
```

---

## Measuring Triage Quality

### What to track per session

- For each conditional agent: DISPATCH or SKIP decision with reasoning
- For dispatched agents: whether they produced findings (hit/miss)
- For skipped agents: no direct way to know if they would have found something

### Triage metrics (via extract-session-metrics.py)

The `extract-session-metrics.py` script should be extended to capture triage decisions from session transcripts:

- Parse `TRIAGE: <agent>: <DISPATCH|SKIP> — <reason>` lines from orchestrator output
- Track per-agent: skip count, dispatch count, hit rate among dispatched
- Compare to pre-triage baselines (from the 47-session analysis)

### Retrospective validation

- Every 20-30 sessions, pick 5 sessions where agents were skipped
- Manually dispatch the skipped agents against the same branch/range
- Check if they would have produced findings
- If a skipped agent would have found confirmed issues, tighten that agent's dispatch criteria

### Success criteria

- Overall dispatch count drops 20-30% without losing confirmed findings
- Hit rate among dispatched agents increases across the board
- Zero cases where a skipped agent would have found a HIGH severity confirmed issue
- Total pipeline token budget decreases by ~15-20%

---

## Implementation Checklist

1. **Update `commands/full-code-review.md`** — insert Step 3.6 triage block between preflight and dispatch
2. **Update `commands/code-review.md`** — same triage block
3. **Update `commands/pr-review.md`** — same triage block (if it has its own dispatch logic)
4. **Update reconciliator** — handle `STATUS=SKIPPED_TRIAGE` signals alongside `STATUS=SKIPPED`
5. **Extend `scripts/extract-session-metrics.py`** — parse triage decisions, add `--triage` report mode
6. **Update `README.md`** — document the triage behavior and the metrics script
7. **Run 10 sessions** — validate triage decisions are reasonable
8. **Retrospective at 30 sessions** — validate no HIGH-severity findings were missed

---

## Related Documents

| Document | Contents |
|---|---|
| `.claude/docs/analysis/2026-02-28-reviewer-agent-value-ranking.md` | 47-session value ranking with hit rates, token budgets, overlap analysis |
| `.claude/docs/analysis/2026-02-28-ingest-validation-analysis.md` | 313-finding precision analysis across 29 sessions |
| `.claude/docs/analysis/2026-02-28-architecture-patterns-coupling.md` | Architecture ↔ patterns overlap analysis and resolution options |
| `plugins/pirategoat-tools/scripts/extract-session-metrics.py` | Session metrics extraction script (to be extended with triage tracking) |

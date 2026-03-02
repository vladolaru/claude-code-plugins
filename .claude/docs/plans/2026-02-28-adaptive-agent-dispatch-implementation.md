# Adaptive Agent Dispatch Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an LLM triage step (Step 3.6) between the existing file-type preflight and agent dispatch, so 6 conditional agents are only dispatched when their criteria match the PR's actual changes.

**Architecture:** The orchestrator (already an LLM) evaluates 6 conditional agents against per-agent dispatch criteria using the diffstat and commit messages it already has from Steps 1-3. Agents that don't match criteria are skipped with `STATUS=SKIPPED_TRIAGE` signal. The `extract-session-metrics.py` script already has triage tracking support — no changes needed there.

**Tech Stack:** Markdown command files (prompt engineering), Python test assertions (pytest)

**Design doc:** `docs/plans/2026-02-28-adaptive-agent-dispatch-design.md`

---

## Agent Classification Reference

| Category | Agents | Gate |
|---|---|---|
| **Always dispatch** | pr-reviewer, patterns-reviewer, history-insights-reviewer | Preflight file-type only |
| **File-type gated** | js-tests, php-tests, e2e-tests, go-tests | Preflight file-type only (existing, works well) |
| **LLM-triaged** | security, dead-code, architecture, wp-architecture, performance, a11y | Preflight file-type → LLM triage |
| **On-demand only** | gemini, codex | Never auto-dispatched |

---

### Task 1: Write Failing Tests for Triage Block

**Files:**
- Modify: `plugins/pirategoat-tools/tests/test_commands.py`

**Step 1: Add `TestTriageBlock` test class**

After the `TestFullCodeReview` class (around line 377), add a new test class that validates both dispatch commands have a properly formed triage block:

```python
# =============================================================================
# Triage Block Tests (Step 3.6: Adaptive Agent Triage)
# =============================================================================


# The 6 agents subject to LLM triage (must match design doc)
TRIAGED_AGENTS = [
    "security-reviewer",
    "dead-code-reviewer",
    "architecture-reviewer",
    "wp-architecture-reviewer",
    "performance-reviewer",
    "a11y-reviewer",
]


class TestTriageBlock:
    """Step 3.6 adaptive agent triage is present and well-formed in dispatch commands."""

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_has_triage_step(self, command):
        """Dispatch commands must contain Step 3.6 triage block."""
        content = _read_command(command)
        assert "Step 3.6" in content or "Adaptive Agent Triage" in content, (
            f"{command}: missing Step 3.6 (Adaptive Agent Triage)"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_lists_all_conditional_agents(self, command):
        """Triage block must reference all 6 conditional agents."""
        content = _read_command(command)
        for agent in TRIAGED_AGENTS:
            # Agent name without -reviewer suffix is acceptable in criteria headings
            agent_base = agent.replace("-reviewer", "")
            assert agent in content or agent_base in content, (
                f"{command}: triage block missing conditional agent '{agent}'"
            )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_output_format(self, command):
        """Triage block must document the TRIAGE: output format."""
        content = _read_command(command)
        assert "TRIAGE:" in content, (
            f"{command}: missing TRIAGE: output format"
        )
        # Must show both DISPATCH and SKIP as possible decisions
        assert "DISPATCH" in content and "SKIP" in content, (
            f"{command}: triage format must show both DISPATCH and SKIP decisions"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_skipped_signal(self, command):
        """Dispatch commands must include STATUS=SKIPPED_TRIAGE signal for reconciliator."""
        content = _read_command(command)
        assert "SKIPPED_TRIAGE" in content, (
            f"{command}: missing STATUS=SKIPPED_TRIAGE signal for reconciliator"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_between_preflight_and_dispatch(self, command):
        """Step 3.6 must appear between Step 3.5 (preflight) and Step 4 (dispatch)."""
        content = _read_command(command)
        pos_35 = content.find("Step 3.5")
        pos_36 = content.find("Step 3.6")
        pos_4 = content.find("Step 4")
        assert pos_35 < pos_36 < pos_4, (
            f"{command}: Step 3.6 must be between Step 3.5 and Step 4 "
            f"(positions: 3.5={pos_35}, 3.6={pos_36}, 4={pos_4})"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_default_is_dispatch(self, command):
        """Triage must default to DISPATCH when in doubt (safety mechanism)."""
        content = _read_command(command).lower()
        assert "when in doubt" in content and "dispatch" in content, (
            f"{command}: triage must specify 'when in doubt, DISPATCH' default"
        )
```

**Step 2: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py::TestTriageBlock -v`
Expected: All 12 tests FAIL (6 parametrized tests × 2 commands)

**Step 3: Commit**

```bash
git add plugins/pirategoat-tools/tests/test_commands.py
git commit -m "$(cat <<'EOF'
test(pirategoat-tools): add structural tests for Step 3.6 adaptive agent triage

The adaptive agent dispatch design (Step 3.6) adds LLM-based triage
between the file-type preflight and agent dispatch. These tests verify
the triage block is present, well-formed, and properly positioned in
both dispatch commands before the implementation is added.

Tests cover: triage step existence, all 6 conditional agents listed,
TRIAGE: output format, SKIPPED_TRIAGE signal, ordering between
Steps 3.5 and 4, and "when in doubt, DISPATCH" default.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

### Task 2: Add Step 3.6 to full-code-review.md

**Files:**
- Modify: `plugins/pirategoat-tools/commands/full-code-review.md:79-113`

**Step 1: Insert Step 3.6 triage block after Step 3.5 (after line 79)**

Insert the following block between the end of Step 3.5 (line 79: `- If \`IS_STALE: false\`: proceed normally, no message needed.`) and Step 4 (line 81: `## Step 4: Dispatch Reviewer Agents in Parallel`):

```markdown
## Step 3.6: Adaptive Agent Triage

Not all agents that pass the file-type preflight need to run. Evaluate the **6 conditional agents** below against the diffstat and commit messages you already have from Steps 1-3.

For each conditional agent that passed the preflight check in Step 3.5:

1. Check its **DISPATCH WHEN** criteria against the diffstat and commit messages
2. Decide: **DISPATCH** or **SKIP**
3. Log your reasoning (required for every decision)

**DEFAULT: When in doubt, DISPATCH.** Only skip when you are confident none of the criteria apply.

### Conditional Agents

**security-reviewer**
DISPATCH WHEN the PR expands or modifies attack surface:
- New or modified endpoints accepting external input
- Code processing user-supplied data (form fields, query params, request bodies, file uploads)
- Database operations (reads, writes, raw queries)
- Dynamic content rendered to output
- Auth, authorization, or session management changes
- File system operations with user-influenced paths
- Third-party API or webhook integrations
- Cryptographic or secret/token handling
- Commits introducing new entry points or data processing

**dead-code-reviewer**
DISPATCH WHEN the PR changes the dependency graph:
- Files deleted or renamed
- Significant code removal (removed > added)
- Refactoring commits (extract, move, rename, consolidate, remove, delete)
- Import/require statements added or removed
- New files replacing or superseding existing ones

**architecture-reviewer**
DISPATCH WHEN the PR introduces structural changes:
- New classes, interfaces, or abstract types added
- Files spanning 3+ architectural layers
- Commits mentioning architecture, refactor, restructure, decouple, extract
- Large PRs (20+ files or 500+ lines)
- New modules or packages introduced

**wp-architecture-reviewer**
DISPATCH WHEN the PR touches WordPress integration points:
- PHP files using WordPress APIs (hooks, filters, options, transients, REST)
- WooCommerce-specific files
- Admin menus, settings pages, or custom post types
- Commits mentioning hooks, filters, backwards compatibility, deprecation, i18n
- Plugin bootstrap or activation/deactivation files

**performance-reviewer**
DISPATCH WHEN the PR touches data flow or rendering:
- Database queries, API calls, data fetching hooks
- Lists, tables, pagination, bulk operations
- Asset loading, lazy loading, code splitting
- Caching logic (transients, object cache, memoization)
- Commits mentioning performance, optimize, cache, query, load time

**a11y-reviewer**
DISPATCH WHEN the PR modifies user-facing UI:
- JSX/TSX with interactive elements (buttons, forms, modals, dropdowns)
- ARIA attributes or focus management code
- New UI components or significant visual changes
- CSS/SCSS affecting visibility, focus indicators, or contrast
- Commits mentioning accessibility, a11y, keyboard, screen reader, ARIA

### Triage Output

For each conditional agent, log:

```
TRIAGE: <agent-name>: <DISPATCH|SKIP> — <one-line reasoning>
```

Example:
```
TRIAGE: security-reviewer: DISPATCH — PR adds new REST endpoint in src/api/users.ts
TRIAGE: dead-code-reviewer: SKIP — no files deleted, no refactoring commits, net +120 lines
TRIAGE: architecture-reviewer: SKIP — single component file changed, no structural reorganization
TRIAGE: wp-architecture-reviewer: DISPATCH — PHP files modify WooCommerce payment gateway hooks
TRIAGE: performance-reviewer: DISPATCH — new useQuery hook in data-fetching layer
TRIAGE: a11y-reviewer: DISPATCH — new modal component with form inputs
```

Agents that passed preflight but are skipped by triage are **not dispatched** in Step 4. They are recorded as `STATUS=SKIPPED_TRIAGE` in the agent signals for the reconciliator.
```

**Step 2: Update Step 4's skip signal annotation (line 113)**

Change line 113 from:
```
Agents not dispatched (domain had no files) are recorded as `STATUS=SKIPPED` in the agent signals for the reconciliator.
```

To:
```
Agents not dispatched due to scope are recorded in agent signals for the reconciliator:
- Preflight skip: `<agent>: STATUS=SKIPPED (no files in <domain> domain)`
- Triage skip: `<agent>: STATUS=SKIPPED_TRIAGE (<one-line reason from Step 3.6>)`
```

**Step 3: Update Step 5 reconciliator signal format (lines 127-128)**

Change the reconciliator prompt signal lines from:
```
    <for each skipped agent: "<agent>: STATUS=SKIPPED (no files in <domain> domain)">
```

To:
```
    <for each preflight-skipped agent: "<agent>: STATUS=SKIPPED (no files in <domain> domain)">
    <for each triage-skipped agent: "<agent>: STATUS=SKIPPED_TRIAGE (<reason from Step 3.6>)">
```

**Step 4: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py::TestTriageBlock -v -k "full-code-review"`
Expected: All 6 full-code-review tests PASS

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`
Expected: All existing tests still pass (no regression)

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/commands/full-code-review.md
git commit -m "$(cat <<'EOF'
feat(pirategoat-tools): add Step 3.6 adaptive agent triage to full-code-review

The file-type preflight (Step 3.5) passes agents through a coarse gate:
security-reviewer dispatches in 81% of sessions but only finds issues
in 11%. Six conditional agents now go through an additional LLM triage
that evaluates per-agent dispatch criteria against the actual diffstat
and commit messages.

Agents are classified as always-dispatch (pr-reviewer, patterns,
history-insights), file-type-gated (test reviewers), or LLM-triaged
(security, dead-code, architecture, wp-architecture, performance,
a11y). Triage defaults to DISPATCH when in doubt, and every skip
decision requires logged reasoning for retrospective validation.

Skipped agents are recorded as STATUS=SKIPPED_TRIAGE (distinct from
the preflight STATUS=SKIPPED) for the reconciliator and metrics
tracking.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

### Task 3: Add Step 3.6 to code-review.md

**Files:**
- Modify: `plugins/pirategoat-tools/commands/code-review.md:107-174`

**Step 1: Insert Step 3.6 triage block after Step 3.5 (after line 107)**

Insert the **identical triage block** from Task 2 between the end of Step 3.5 (line 107: `- If \`IS_STALE: false\`: proceed normally, no message needed.`) and Step 4 (line 109: `## Step 4: Dispatch Reviewer Agents in Parallel`).

The content is exactly the same as Task 2, Step 1 — copy it verbatim.

**Step 2: Update Step 4's skip signal annotation (line 141)**

Same change as Task 2, Step 2.

**Step 3: Update Step 6 reconciliator signal format (lines 173-174)**

Same change as Task 2, Step 3 — update the reconciliator prompt signal lines.

**Step 4: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py::TestTriageBlock -v`
Expected: All 12 tests PASS (both commands)

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/commands/code-review.md
git commit -m "$(cat <<'EOF'
feat(pirategoat-tools): add Step 3.6 adaptive agent triage to code-review

Same triage block as full-code-review — both dispatch commands now
evaluate 6 conditional agents (security, dead-code, architecture,
wp-architecture, performance, a11y) against per-agent dispatch
criteria before dispatching.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

### Task 4: Update Reconciliator Agent Names

**Files:**
- Modify: `plugins/pirategoat-tools/agents/review-reconciliator.md:71`

**Step 1: Add missing agents to `agent_names` list**

The reconciliator's `agent_names` list (line 71) is missing `dead-code` and `go-tests`. These agents write output files but the reconciliator doesn't read them.

Change line 71 from:
```python
agent_names = ['security', 'architecture', 'wp-architecture', 'performance', 'php-tests', 'js-tests', 'e2e-tests', 'patterns', 'history-insights', 'pr', 'tests-mutation', 'a11y']
```

To:
```python
agent_names = ['security', 'architecture', 'wp-architecture', 'performance', 'php-tests', 'js-tests', 'e2e-tests', 'go-tests', 'patterns', 'history-insights', 'pr', 'tests-mutation', 'dead-code', 'a11y']
```

**Step 2: Add `dead-code-review` and `go-tests-review` to the file tree in the doc (lines 33-51)**

Update the file tree comment to include the missing files:

```
├── go-tests-review.json/.md
```
after `e2e-tests-review.json/.md`, and:
```
├── dead-code-review.json/.md
```
after `tests-mutation-review.json/.md`.

**Step 3: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add plugins/pirategoat-tools/agents/review-reconciliator.md
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): add dead-code and go-tests to reconciliator agent_names

The reconciliator's agent_names list was missing dead-code-reviewer and
go-tests-reviewer. These agents write output files but the reconciliator
was not reading them, causing their findings to be silently dropped from
the reconciled summary.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

### Task 5: Fix pr-review.md Stale Agent Count

**Files:**
- Modify: `plugins/pirategoat-tools/commands/pr-review.md:34`

**Step 1: Fix the stale "12 agents" reference**

Change line 34 from:
```
| Step 8 (Agent dispatch) | **Use `/full-code-review` dispatch** (steps 3.5–5) instead of the skill's selective dispatch — ensures all 12 agents run regardless of PR size |
```

To:
```
| Step 8 (Agent dispatch) | **Use `/full-code-review` dispatch** (steps 3.5–5) instead of the skill's selective dispatch — ensures all eligible agents run with triage regardless of PR size |
```

**Step 2: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py::TestPrReview -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add plugins/pirategoat-tools/commands/pr-review.md
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): fix stale agent count in pr-review Step 8 override

The override table said "all 12 agents" but there are 13 agents in the
dispatch table (a11y-reviewer was added). Changed to "all eligible
agents run with triage" which is both accurate and future-proof — it
no longer hard-codes a count and reflects the new triage step.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

### Task 6: Version Bump and Changelog

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:8`
- Modify: `.claude-plugin/marketplace.json:16`

**Step 1: Add changelog entry**

Insert a new version section before `## [1.37.1]` (line 8):

```markdown
## [1.38.0] - 2026-02-28

### Added

- **Adaptive agent dispatch (Step 3.6)** — LLM triage step between file-type preflight and agent dispatch. Six conditional agents (security, dead-code, architecture, wp-architecture, performance, a11y) are now evaluated against per-agent dispatch criteria using the diffstat and commit messages. Agents that don't match criteria are skipped with `STATUS=SKIPPED_TRIAGE` signal, reducing wasted token budget by ~20-30% without losing confirmed findings. Triage defaults to DISPATCH when in doubt to maintain safety.

### Fixed

- **Reconciliator missing dead-code and go-tests agents** — The reconciliator's `agent_names` list was missing `dead-code` and `go-tests`, causing their findings to be silently dropped from reconciled summaries.
- **pr-review.md stale agent count** — Updated Step 8 override from hard-coded "12 agents" to "all eligible agents with triage."

```

**Step 2: Bump version in marketplace.json**

Change line 16 from:
```json
      "version": "1.37.1",
```

To:
```json
      "version": "1.38.0",
```

**Step 3: Commit**

```bash
git add plugins/pirategoat-tools/CHANGELOG.md .claude-plugin/marketplace.json
git commit -m "$(cat <<'EOF'
feat(pirategoat-tools): bump to v1.38.0 for adaptive agent dispatch

Step 3.6 adaptive agent triage is a new feature (minor version bump).
Also includes two bug fixes: reconciliator missing dead-code/go-tests
agents, and stale agent count in pr-review.md.

Refs adaptive-agent-dispatch-design
EOF
)"
```

---

## Summary

| Task | What | Files | Type |
|---|---|---|---|
| 1 | Write failing tests for triage block | `tests/test_commands.py` | test |
| 2 | Add Step 3.6 to full-code-review.md | `commands/full-code-review.md` | feat |
| 3 | Add Step 3.6 to code-review.md | `commands/code-review.md` | feat |
| 4 | Fix reconciliator agent_names | `agents/review-reconciliator.md` | fix |
| 5 | Fix pr-review.md stale count | `commands/pr-review.md` | fix |
| 6 | Version bump + changelog | `CHANGELOG.md`, `marketplace.json` | chore |

**Not changed (already done):** `scripts/extract-session-metrics.py` — already has `TRIAGED_AGENTS` list and `extract_triage_decisions()` function with full parsing support for `TRIAGE:` lines and `STATUS=SKIPPED_TRIAGE` signals.

**Post-implementation validation (not code tasks):**
- Run 10 review sessions to validate triage decisions are reasonable
- Retrospective at 30 sessions to verify no HIGH-severity findings were missed by triage skips

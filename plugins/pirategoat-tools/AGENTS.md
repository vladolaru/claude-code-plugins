# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings, and ingest results through a multi-phase validation pipeline.

## Key Files

**IMPORTANT: `scripts/agent-registry.json` is the single source of truth for all reviewer agent configuration.** Every agent change starts and ends here.

| File | Role |
|------|------|
| `scripts/agent-registry.json` | Agent registry — domain, protocols, dispatch class, triage criteria, model tier. |
| `scripts/bootstrap-reviewer.py` | Builds the structured prompt each agent receives. Handles plugin root discovery, protocol extraction, scope discovery, and output instructions. |
| `scripts/review-scope.py` | Efficient diff scoping. Filters changes by domain (security, performance, php-tests, etc.) and outputs structured STATUS/FILES/STATS/DIFFS sections. |
| `scripts/plan-review-dispatch.py` | Deterministic dispatch planning. Reads agent registry + changed files → produces which agents to run, skip, and why. Used by all review commands. |
| `scripts/reconcile-reviews.py` | Deduplicates and merges findings from all agents into `reconciled-structured.json`. |
| `scripts/ingest-preprocess.py` | Deterministic pre-classification before LLM-based verification. Assigns stable IDs, checks scope against diff hunks. |
| `scripts/ingest-code-review.py` | 3-step LLM pipeline for finding validation (Chain-of-Verification pattern). |
| `scripts/review_output_simple.py` | ReviewOutputBuilder — `add_issue()`, `add_recommendation()`, `add_positive()`, verdict calculation, JSON/Markdown serialization. |
| `agents/shared/reviewer-protocol.md` | Shared behavioral rules for all reviewer agents. Bootstrap extracts sections via skip-list. |
| `agents/shared/tests-reviewer-protocol.md` | Additional rules for test reviewer agents (test quality principles, anti-patterns). |
| `schemas/review-output.ts` | TypeScript type definitions for structured review output (Issue, SecurityIssue, PerformanceIssue, etc.). |

## Architecture

### Review Pipeline

```
Command (orchestrator)
  │
  ├─ plan-review-dispatch.py → dispatch plan (which agents, why)
  │
  ├─ For each agent (parallel):
  │   │
  │   └─ bootstrap-reviewer.py
  │       ├─ Extracts protocol sections (skip-list)
  │       ├─ Runs review-scope.py (domain-filtered diff)
  │       └─ Builds structured prompt:
  │           Section 1: REVIEW RULES    (top — primacy effect)
  │           Section 2: REVIEW CONTENT  (middle — processing zone)
  │           Section 3: OUTPUT          (bottom — recency effect)
  │
  ├─ reconcile-reviews.py → reconciled-structured.json
  │
  └─ ingest pipeline (on user request):
      ├─ ingest-preprocess.py (deterministic)
      └─ ingest-code-review.py (3-step LLM verification)
```

### Shared Protocols

**reviewer-protocol.md** provides behavioral rules for all agents. Bootstrap extracts it via a **skip-list** — sections the bootstrap already handles are excluded, everything else is included automatically. New sections added to the protocol are picked up without code changes.

Skip-list (sections bootstrap replaces with concrete values):
- `## Step 0` (plugin root — bootstrap resolved it)
- `## Scope Discovery` (bootstrap ran review-scope.py)
- `## Output Directory` (bootstrap resolved to concrete path)
- `## ReviewOutputBuilder API` (bootstrap provides pre-filled snippet)
- `## File-Based Output` (bootstrap provides concrete file paths)

**tests-reviewer-protocol.md** is appended for agents with `"tests-reviewer"` in their `protocols` list. It adds test quality principles (RULE 0: tests verify behavior, not implementation) and common anti-patterns.

### Bootstrap Output Positioning

The prompt bootstrap builds uses deliberate section ordering. Preserve this order when modifying `bootstrap-reviewer.py` or protocol files:

1. **REVIEW RULES** (top) — behavioral steering via primacy effect. Agent reads rules first, anchoring behavior.
2. **REVIEW CONTENT** (middle) — the actual diff/scope. Processing zone where the agent does its work.
3. **OUTPUT INSTRUCTIONS** (bottom) — format and file paths. Recency effect ensures the agent remembers how to produce output.

## Agent Registry

`scripts/agent-registry.json` configures all reviewer agents. Each entry:

| Field | Required | Description |
|-------|----------|-------------|
| `domain` | yes | Scope domain for `review-scope.py` filtering. `null` for agents that don't use scope (e.g., tests-mutation-reviewer). |
| `protocols` | yes | List of protocol files to include: `"reviewer"` (all agents), `"tests-reviewer"` (test agents). |
| `scope_flags` | yes | Extra flags passed to `review-scope.py` (e.g., `["--max-lines", "500"]`). Empty list `[]` for defaults. |
| `dispatch_class` | yes | When agent runs — see dispatch classes below. |
| `focus` | yes | One-line description of the agent's review focus. |
| `model_tier` | yes | `"inherit"` (caller's model), `"sonnet"`, or `"haiku"`. Match reasoning depth needed. |
| `triage_criteria` | conditional | Required for `dispatch_class: "conditional"`. List of conditions that trigger dispatch. |
| `secondary_domains` | optional | Additional scope domains to include (e.g., `["config-ops"]`). |
| `extra_scope` | optional | Additional scope invocations (e.g., `["--base-ref-only"]` for patterns-reviewer). |
| `file_history` | optional | If `true`, bootstrap includes git history per changed file. |
| `max_history_commits` | optional | How many commits of history per file (default: 5). |

### Dispatch Classes

| Class | Behavior |
|-------|----------|
| `always` | Auto-dispatched on every review |
| `conditional` | Dispatched only when triage criteria match the diff |
| `manual` | Only on explicit user request |
| `special` | Orchestration/synthesis agents, not dispatched by triage |

Commands handle triage at step 3.6 — they check each conditional agent's `triage_criteria` against the diffstat and commit messages.

## Output Contract

Each reviewer agent produces two files in `OUTPUT_DIR`:

- `<agent-name>.json` — structured findings using `ReviewOutputBuilder` (see `schemas/review-output.ts` for types)
- `<agent-name>.md` — human-readable Markdown summary

**ReviewOutputBuilder API** (`scripts/review_output_simple.py`):

```python
from review_output_simple import ReviewOutputBuilder
builder = ReviewOutputBuilder(reviewer="security-reviewer", pr_id="123")
builder.add_issue(severity="high", category="xss", title="...", description="...", file="...", line=42, recommendation="...")
builder.add_positive("Good input validation on...")
builder.add_recommendation("immediate", "Fix the XSS vulnerability")
output = builder.build()  # Returns dict with verdict, summary, issues, etc.
```

Verdict is auto-calculated from issue severities: any critical → `block`, any high → `request_changes`, any medium → `comment`, otherwise → `approve`.

## Development Workflows

### Adding a Reviewer Agent

1. Read existing agent `.md` files in `agents/` to understand the format and conventions
2. Create `agents/<agent-name>.md` with the agent definition
3. Add entry to `scripts/agent-registry.json` — choose domain, protocols, dispatch class, model tier, and (if conditional) triage criteria
4. Add agent to `.claude-plugin/marketplace.json` in the `agents` array
5. Run tests: `pytest plugins/pirategoat-tools/tests/ -v` (parameterized tests auto-include new agents)

### Adding a Command

1. Read existing commands in `commands/` to understand the dispatch pattern
2. Create `commands/<command-name>.md` — commands are orchestrators that invoke agents via the `/Agent` tool
3. Use `plan-review-dispatch.py` for triage decisions (don't duplicate triage logic)
4. Add command to `.claude-plugin/marketplace.json` in the `commands` array
5. Run tests: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`

### Adding a Skill

1. Create `skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and Markdown body
2. Add skill to `.claude-plugin/marketplace.json` in the `skills` array

### Expected Failures

These are normal — handle them, do not stop or apologize:

- **`review-scope.py` returns empty scope**: The diff has no files matching this agent's domain. Skip the agent — this is correct triage behavior.
- **Tests fail after your changes**: Read the failure output, fix the root cause, and re-run. Test failures are feedback, not errors.
- **`bootstrap-reviewer.py` can't find plugin root**: Ensure you are running from within the repository. The script walks up from CWD looking for `.claude-plugin/`.

### Testing

**Always run tests after modifying scripts, agents, or commands.** See the root `AGENTS.md` [Testing > pirategoat-tools](#pirategoat-tools) section for the full test lookup table, test principles, and agent compliance eval commands.

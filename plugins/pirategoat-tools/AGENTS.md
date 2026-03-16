# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings through semantic deduplication and verification, then stress-test conclusions via an independent decision critic.

## Key Files

**IMPORTANT: `scripts/agent-registry.json` is the single source of truth for all reviewer agent configuration.** Every agent change starts and ends here.

| File | Role |
|------|------|
| `scripts/agent-registry.json` | Agent registry — domain, protocols, dispatch class, triage criteria, model tier. |
| `scripts/bootstrap-reviewer.py` | Builds the structured prompt each agent receives. Handles plugin root discovery, protocol extraction, scope discovery, and output instructions. |
| `scripts/review-scope.py` | Efficient diff scoping. Filters changes by domain (security, performance, php-tests, etc.) and outputs structured STATUS/FILES/STATS/DIFFS sections. |
| `scripts/plan-review-dispatch.py` | Deterministic dispatch planning. Reads agent registry + changed files → produces which agents to run, skip, and why. Used by all review commands. |
| `scripts/review_output_simple.py` | ReviewOutputBuilder — `add_issue()`, `add_recommendation()`, `add_positive()`, verdict calculation, JSON/Markdown serialization. |
| `scripts/review-telemetry.py` | JSONL telemetry logging. `ReviewTelemetry` class captures pipeline timing, agent start/complete lifecycle, snapshots, and summaries. |
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
  ├─ review-reconciliator agent (semantic dedup + scope check + fact verification)
  │   └─ Produces review-findings.json + review-findings.md
  │
  └─ decision-reviewer agent (independent stress test)
      └─ Produces decision-critic-findings.md with STAND/REVISE/ESCALATE verdict
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

Verdict is auto-calculated from issue severities:
- Any critical → `block`
- 3+ highs → `block`
- Any high (or 5+ mediums) → `request_changes`
- Any medium → `comment`
- Otherwise → `approve`

### Cross-Repo Dependency: pirategoat-bot

The `pirategoat-bot` Slack bot (at `~/Work/a8c/pirategoat-bot`) wraps this plugin's review pipeline. The two repos share integration contracts that must stay in sync:

- **`review-context.json`** — The bot writes this file (orchestrator.js) before spawning the `claude` CLI. This plugin reads and enriches it (gather-review-context.py). Field names, nesting, and required paths must match across both repos.
- **Outer-pipeline verdict values** — The bot's `pr-review.py` defines outer-pipeline verdicts (`APPROVE`/`COMMENT`/`REQUEST_CHANGES`) and `github.js` maps them to GitHub actions. This plugin has its own per-agent verdict system (`block`/`request_changes`/`comment`/`approve` in `review_output_simple.py`). These are distinct layers — changes to one may need corresponding changes in the other.
- **Prompt template variables** — The bot's `prompts/pr-review.md` injects variables (`{{MERGE_BASE}}`, `{{GIT_RANGE}}`, etc.) that this plugin's scripts consume via the review context.

**Rule: Before changing any integration surface in this plugin, read the corresponding code in pirategoat-bot first.** Do not assume the bot's expectations from this plugin's code alone — check the bot's actual implementation. When in doubt, read:
- `pirategoat-bot/src/orchestrator.js` (writes review-context.json, reads review output)
- `pirategoat-bot/src/github.js` (maps verdicts to GitHub actions)
- `pirategoat-bot/scripts/pr-review.py` (outer-pipeline prompt and verdict logic)

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
5. Add structural tests in `tests/test_commands.py` (new `TestXxx` class)
6. Run tests: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`
7. **Update all docs** — see [Doc Update Checklist](#doc-update-checklist-for-new-commands-skills-or-agents) below

### Adding a Skill

1. Create `skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and Markdown body
2. Add skill to `.claude-plugin/marketplace.json` in the `skills` array
3. **Update all docs** — see [Doc Update Checklist](#doc-update-checklist-for-new-commands-skills-or-agents) below

### Expected Failures

These are normal — handle them, do not stop or apologize:

- **`review-scope.py` returns empty scope**: The diff has no files matching this agent's domain. Skip the agent — this is correct triage behavior.
- **Tests fail after your changes**: Read the failure output, fix the root cause, and re-run. Test failures are feedback, not errors.
- **`bootstrap-reviewer.py` can't find plugin root**: Ensure you are running from within the repository. The script walks up from CWD looking for `.claude-plugin/`.

### Testing

**Always run tests after modifying scripts, agents, or commands.** See the root `AGENTS.md` [Testing > pirategoat-tools](#pirategoat-tools) section for the full test lookup table, test principles, and agent compliance eval commands.

### Doc Update Checklist for New Commands, Skills, or Agents

**Every new command, skill, or agent requires updates in all four locations below.** Do not skip — stale counts and missing entries make the plugin inventory unreliable.

| # | File | What to update |
|---|------|----------------|
| 1 | `.claude-plugin/marketplace.json` | Add entry to the plugin's `commands`, `skills`, or `agents` array |
| 2 | `plugins/pirategoat-tools/README.md` | Update count in directory tree + add row to the relevant table |
| 3 | Root `AGENTS.md` → Plugin Inventory → pirategoat-tools | Update summary count + add to the `commands/`/`skills/`/`agents/` contents row |
| 4 | Root `README.md` | Update count in directory tree (e.g., "19 agents, 19 skills, 7 commands") |

# AGENTS.md

You maintain a Claude Code plugin marketplace. This file is your operating manual — follow it when working on any plugin in this repo.

## Repository Overview

This is **vladolaru-claude-code-plugins** - Vlad Olaru's personal Claude Code plugin marketplace featuring specialized plugins for development workflows, WordPress backend development, and AI-powered tools.

**Development model:** This project is AI-written and AI-maintained with human guidance and decisions. The human (Vlad) sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance. "Single maintainer" does not mean capacity-constrained — it means single human decision-maker with AI execution capacity. Do not assume limited implementation bandwidth when reasoning about priorities or feasibility.

## Architecture

### Plugin Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── CLAUDE.md                     # Claude Code shim -> @AGENTS.md
├── AGENTS.md                     # Canonical shared instructions
├── plugins/
│   └── plugin-name/
│       ├── CHANGELOG.md          # Version history
│       ├── agents/               # Subagent definitions (optional)
│       │   └── agent-name.md
│       ├── commands/             # Slash commands (optional)
│       │   └── command.md
│       ├── skills/               # Skills with SKILL.md files (optional)
│       │   └── skill-name/
│       │       └── SKILL.md
│       └── scripts/              # Helper scripts (optional)
├── LICENSE
└── README.md
```

### Skills Specification

All skills must have a `SKILL.md` file with YAML frontmatter:

- **Required frontmatter fields**:
  - `name` - hyphen-case, lowercase alphanumeric + hyphens
  - `description` - when Claude should use this skill
- **Optional frontmatter fields**:
  - `license`
  - `metadata` - custom key-value pairs
- **Body**: Markdown instructions, examples, and guidelines

## Plugin Inventory

### pirategoat-tools

Code review orchestration with 18 parallel review agents, 19 skills, and 6 commands covering security, architecture, testing, WordPress, accessibility, and more.

| Directory | Contents |
|---|---|
| `agents/` | 18 review agent definitions + 2 shared protocols in `agents/shared/` |
| `skills/` | 19 reference skills (testing patterns, software architecture, WordPress, browser interaction, Figma, etc.) |
| `commands/` | 6 slash commands (`/pr-review`, `/full-code-review`, `/code-review`, `/ingest-code-review`, `/pr-update`, `/copy-as`) |
| `scripts/` | Bootstrap reviewer, ground truth integrators (Jest, PHPUnit, Playwright, ESLint, PHPCS, Semgrep, Bandit), review output, session metrics |
| `schemas/` | JSON output schemas for review results |
| `tests/` | Deterministic eval suite — see [Testing](#pirategoat-tools-1) section |

**Dev notes:** Agents run in parallel by default. Model tier assignment matters (`inherit`/`sonnet`/`haiku` based on reasoning depth). Ground truth tools (linters, scanners) are optional — agents fall back to manual analysis.

### dex

Knowledge capture system — turns lessons, patterns, decisions, and research into agent-first documents that compound engineering work.

| Directory | Contents |
|---|---|
| `commands/` | 7 commands: `/dex:grok`, `/dex:learn`, `/dex:pattern`, `/dex:research`, `/dex:sharpen`, `/dex:init`, `/dex:status` |
| `skills/` | 1 skill (`knowledge-capture`) — core logic shared by all commands |

**Dev notes:** Adapts to host project automatically (`CLAUDE.md` + `.claude/docs/` or `AGENTS.md` + `.ai/docs/`). Instructions file has a 550-line budget enforced; promotes rules as one-liners with links to full docs. No external dependencies.

### prompt-engineer

Systematic prompt optimization with evidence-grounded technique recommendations from a 50+ technique reference library. Human-in-the-loop gates between phases.

| Directory | Contents |
|---|---|
| `skills/` | 1 skill (`prompt-engineer`) — 5-phase workflow: Triage → Analysis → Selection → Optimization → Verification |
| `commands/` | 1 command (`/optimize-prompt`) |

**Dev notes:** Simple prompts stay simple (triage filters out low-complexity cases). Works on any prompt format (SKILL.md, agent definitions, slash commands, CLAUDE.md, API prompts). No external dependencies.

### image-optimizer

Lossless image optimization for PNG, JPEG, GIF, SVG with before/after review and confirmation gate.

| Directory | Contents |
|---|---|
| `commands/` | 1 command (`/optimize-images`) |

**Dev notes:** Requires `imageoptim-cli` (macOS only, via npm) for raster images and/or `svgo` (cross-platform, via npm) for SVGs. At least one must be installed.

### yoloing-safe

YOLO mode safety net — PreToolUse hook that blocks destructive commands, asks confirmation on risky operations, and nudges toward safer alternatives. Has its own `CLAUDE.md` and `AGENTS.md` with detailed development instructions.

| File/Directory | Contents |
|---|---|
| `scripts/pre-tool-use-safety.py` | **Single source of truth** — RULES dict, detection logic, allowlist, all runtime behavior |
| `hooks/hooks.json` | Hook registration for PreToolUse event |
| `tests/` | Unit, integration, meta, and e2e tests |
| `AGENTS.md` | Full development instructions, rule workflows, testing |

**Dev notes:** Four tiers, first-match-wins: allowlist → block (exit 2) → ask (JSON permission prompt) → allow (silent). Self-protection: blocks its own config from modification (hardcoded, undisableable). Fails open — if the hook errors, the tool call proceeds. 5-second timeout. See [Testing](#yoloing-safe-1) section. Python stdlib only.

### caffeinate-claude

Keeps your Mac awake during Claude Code sessions using macOS `caffeinate`. Supports multiple tabs, handles crashes gracefully.

| File/Directory | Contents |
|---|---|
| `hooks/` | `UserPromptSubmit` (starts caffeinate), `Stop` (kills when no sessions remain) |

**Dev notes:** Multi-tab support via session markers (`$PPID` per tab). Auto-prunes markers for dead processes. 1-hour safety timeout as failsafe. macOS-only. No external dependencies.

## Creating New Plugins

1. Create directory: `plugins/<name>/` with subdirs as needed (`skills/`, `commands/`, `agents/`, `scripts/`)
2. Add a `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/) format
3. Register in `.claude-plugin/marketplace.json` — follow the structure of existing entries

## Testing

### pirategoat-tools

The `plugins/pirategoat-tools/tests/` directory contains deterministic evals (no model calls) for plugin scripts. See `tests/TESTING.md` for the full framework documentation: architecture, design principles, how to add tests/graders/scenarios, and conventions.

**When to run tests:** After modifying any of these files, run the relevant test suite:

| Changed file | Run |
|---|---|
| `scripts/bootstrap-reviewer.py` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `agents/shared/reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `agents/shared/tests-reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `scripts/review_output_simple.py` | `pytest plugins/pirategoat-tools/tests/test_graders.py -v` |
| `tests/graders.py` | `pytest plugins/pirategoat-tools/tests/test_graders.py -v` |
| Any reviewer agent `.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` (verifies agent config still works) |
| New agent added to `AGENT_CONFIG` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` (auto-included in all parameterized tests) |
| Any review command `.md` | `pytest plugins/pirategoat-tools/tests/test_commands.py -v` (validates structure, agent refs, script refs) |
| `.claude-plugin/marketplace.json` | `pytest plugins/pirategoat-tools/tests/test_commands.py -v` (validates command registration, agent cross-refs) |

**Run all tests:** `pytest plugins/pirategoat-tools/tests/ -v`

**Test principles:**
- Code-based graders only (fast, deterministic, no model calls)
- Grade outcomes not paths
- Test both positive and negative cases
- Integration tests run `bootstrap-reviewer.py` via subprocess against all registered agents

**Agent compliance eval** (requires `claude` CLI, makes model calls):
```bash
# Grade existing review output files
python3 plugins/pirategoat-tools/tests/eval_agent_compliance.py --grade-only /tmp/pr-review-<N>

# Full dispatch eval (slow, model calls)
python3 plugins/pirategoat-tools/tests/eval_agent_compliance.py --dispatch --agent security-reviewer
```

### yoloing-safe

See `plugins/yoloing-safe/AGENTS.md` for testing instructions, rule workflows (add/remove/rename/retier), and the full RULES dict specification.

**Quick reference:** `pytest plugins/yoloing-safe/tests/ -v`

## Versioning & Releases

### RULE 0: Every Change Gets Documented

Every commit that modifies plugin behavior (features, fixes, refactors, performance) **must** include:

1. **CHANGELOG.md update** — Add an entry under the appropriate version section in the plugin's `CHANGELOG.md`
2. **Version bump in marketplace.json** — Update the plugin's `version` field following semver (`feat` = minor, `fix`/`refactor`/`perf` = patch, `BREAKING CHANGE` = major)

**Coalescing rule:** If the latest version bump has not been pushed to the remote yet, fold new changes into the same version entry rather than bumping again — provided they are of similar impact (e.g., two fixes, or a feature and a closely related fix). If the new change is a higher semver impact (e.g., existing unpushed patch + new feature), upgrade the version to match.

**Exempt from version bumps:** `docs`, `test`, `ci`, `style`, `chore` commits that don't change runtime behavior. Still add a changelog entry if the change is notable.

### Plugin-Prefixed Tags

Since this repository may contain multiple plugins with independent version cycles, use **plugin-prefixed tags**:

**Tag Format:** `<plugin-name>/v<semver>`

**Examples:**
- `pirategoat-tools/v1.0.0`
- `pirategoat-tools/v1.1.0`

### Release Process

1. Update the plugin's `CHANGELOG.md` (Keep a Changelog format)
2. Bump `version` in `.claude-plugin/marketplace.json`
3. Commit, then tag: `<plugin-name>/vX.Y.Z`
4. Optionally create a GitHub Release with `gh release create`

## AI Artifacts

All AI-generated artifacts (plans, analysis, research, decisions, learnings) go under `.claude/docs/`:

```text
.claude/docs/
├── analysis/     # Research findings, investigations, session analysis
├── decisions/    # Architecture Decision Records
├── learnings/    # Debugging insights, gotchas, fixes
├── patterns/     # Reusable workflows, conventions, anti-patterns
├── plans/        # Implementation plans
└── research/     # Deep-dive research (e.g., a11y/, figma/)
```

**RULE:** Never create AI artifacts under `docs/` at the repo root or under `plugins/*/docs/`. Those locations are for committed documentation that ships with the plugin (guides, READMEs). Working artifacts from agent sessions go in `.claude/docs/`.

## Reference

**Design patterns** live in `docs/patterns/`. Consult them before implementing similar functionality:

| Pattern | When to use |
|---|---|
| [step-by-step-prompt-injection](docs/patterns/step-by-step-prompt-injection.md) | Multi-phase analytical workflows where later steps must be independent of earlier conclusions — e.g., verify before judge, gather before synthesize. Includes script template, skill file structure, testing checklist, and two reference implementations. |

**Knowledge capture:** After significant debugging sessions, architectural decisions, or discovering non-obvious behavior, suggest using `/dex:grok` to capture the knowledge.

**License:** MIT — see LICENSE file.

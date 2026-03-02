# pirategoat-tools

My main Claude Code plugin — the Swiss army knife I reach for on every project. Started as a personal grab bag of experimental features and grew into a proper toolkit for code review, testing, architecture, and WordPress development.

Everything here is opinionated, actively used, and evolving.

## What's Inside

### 19 Review Agents

These run in parallel by default — total review time equals the slowest agent, not the sum of all agents.

| Agent | Focus | Model |
|-------|-------|-------|
| **pr-reviewer** | Generalist — validates changes against stated goals, catches cross-cutting issues | inherit |
| **security-reviewer** | WordPress security — SQL injection, XSS, CSRF, capabilities, sanitization | sonnet |
| **architecture-reviewer** | Design patterns, SOLID principles, coupling/cohesion (language-agnostic) | sonnet |
| **wp-architecture-reviewer** | WordPress-specific — hooks, extensibility, WPCS, backwards compatibility | sonnet |
| **performance-reviewer** | N+1 queries, caching, autoloaded options, WP_Query optimization | sonnet |
| **php-tests-reviewer** | PHPUnit test quality, WordPress factories, WooCommerce patterns | sonnet |
| **js-tests-reviewer** | Jest/Vitest quality, React Testing Library queries, async patterns | sonnet |
| **e2e-tests-reviewer** | Playwright quality — locators, Page Object Model, auto-waiting | sonnet |
| **go-tests-reviewer** | Go testing idioms, table-driven tests, httptest, benchmarks | haiku |
| **patterns-reviewer** | Codebase archaeology — finds existing patterns, prevents reinventing the wheel | sonnet |
| **dead-code-reviewer** | Unused functions, unreachable paths, orphaned imports | sonnet |
| **history-insights-reviewer** | Mines git history for relevant prior fixes and lessons learned | sonnet |
| **tests-mutation-reviewer** | Fault injection to verify tests catch real bugs (runs solo) | sonnet |
| **gemini-reviewer** | Cross-validates via Google Gemini CLI for independent perspective | haiku |
| **codex-reviewer** | Cross-validates via OpenAI Codex CLI for independent perspective | haiku |
| **review-reconciliator** | Aggregates findings from all agents into a single prioritized summary | inherit |
| **a11y-reviewer** | ARIA correctness, keyboard access, focus management, WCAG 2.2 AA | inherit |
| **reliability-reviewer** | Logging, error handling, rollback safety, feature flags, failure-mode resilience | sonnet |
| **technical-writer** | Creates documentation after feature completion | haiku |

#### Model Tiers

Not all review work requires the same level of reasoning. Agents are assigned to model tiers based on what their task actually demands:

- **inherit** (3 agents) — Deep judgment work that uses whatever model Claude Code is running (typically Opus). The pr-reviewer must understand PR intent and exercise nuanced blocker-vs-preference decisions. The a11y-reviewer needs contextual reasoning about accessibility impact. The review-reconciliator performs judgment-heavy synthesis — conflict resolution, deduplication, and 10:1 compression across all agent outputs.
- **sonnet** (12 agents) — Structured analysis against well-defined checklists. Architecture reviewers apply SOLID principles and WordPress ecosystem patterns. Security tracing follows a source-to-sink framework. Performance detection matches known antipatterns (N+1, unbounded queries). The reliability reviewer checks error handling, rollback safety, and observability against concrete checklists. Test reviewers check against catalogued smells. The patterns and history-insights reviewers search for codebase precedents. The mutation reviewer follows a rigid 5-phase protocol. The dead-code reviewer traces dependency graphs. All of these benefit from competence but don't need the deep ambiguity-resolution that the most capable models provide.
- **haiku** (4 agents) — Orchestration or highly mechanical work. The gemini and codex reviewers just build prompts, shell out to external CLIs, and parse responses. The technical writer fills token-constrained templates. The go-tests-reviewer matches against Go's highly standardized testing idioms — nearly every finding maps to a known pattern.

### 19 Skills

| Skill | What it brings |
|-------|---------------|
| **testing-patterns** | Test quality patterns with a 190KB reference library — philosophy, smells, TDD workflow |
| **php-testing-patterns** | PHPUnit assertions, WordPress test utilities, WooCommerce patterns, Brain Monkey |
| **js-testing-patterns** | Jest/Vitest assertions, React Testing Library queries, async patterns, snapshots |
| **e2e-testing-patterns** | Playwright locators, Page Object Model, auto-waiting, network interception |
| **go-testing-patterns** | Standard testing package, table-driven tests, httptest, benchmarks, fuzz testing |
| **software-architecture** | GoF patterns, SOLID, hexagonal architecture with an 87KB pattern library |
| **wordpress-backend-dev** | WPCS coding standards, security patterns, i18n, hooks API, REST API |
| **pr-reviewing** | Structured PR review workflow with parallel agent spawning |
| **browser-interaction** | Browser automation via MCP servers (chrome-devtools, playwright) |
| **woocommerce-browser-interaction** | WooCommerce-specific browser workflows — login, admin, checkout |
| **dig-into-linear-issue** | Linear issue investigation with RCA templates |
| **date-time-wrangling** | Time zone reasoning, date calculations, temporal logic for scheduling |
| **decision-critic** | Structured decision analysis for technical trade-offs |
| **creating-md-slides** | Markdown presentations via Marp (PDF, PPTX, HTML) |
| **marp-slide-quality** | SlideGauge integration for presentation analysis |
| **accessible-frontend-dev** | ARIA correctness, keyboard operability, focus management, WCAG 2.2 AA |
| **using-figma** | Figma-to-code workflow — survey, specification, component tree, implementation, validation |
| **figma-copy-sync** | Synchronize text copy between Figma designs and implemented code |
| **analyzing-cc-sessions** | Parse CC session JSONL transcripts, analyze subagent behavior, extract metrics |

### 6 Commands

| Command | Purpose |
|---------|---------|
| `/full-code-review` | Run all review agents in parallel on current branch changes |
| `/code-review` | Incremental review of new commits since the last review |
| `/ingest-code-review` | Analyze review findings, filter false positives, propose action plan |
| `/pr-review` | End-to-end PR review pipeline (context + agents + validation) |
| `/pr-update` | Update PR description with accurate summary of current changes |
| `/copy-as [content] [slack\|p2]` | Copy content to clipboard — markdown, Slack mrkdwn, or P2 HTML |

### Ground Truth Integration

Agents don't guess — they use actual tool outputs when available. Scripts for integrating linters, coverage, and security scanners:

| Phase | Tools | Scripts |
|-------|-------|---------|
| Tests | Jest, PHPUnit, Playwright | `run-tests-for-review.sh` + `parse-test-results.py` |
| Linters | ESLint, PHPCS | `run-linters-for-review.sh` + `parse-linter-results.py` |
| Coverage | Jest, PHPUnit (Xdebug/PCOV) | `run-coverage-for-review.sh` + `parse-coverage-results.py` |
| Security | Semgrep, Bandit | `run-security-scanners-for-review.sh` + `parse-security-results.py` |

All tools are optional — agents fall back to manual analysis when tools aren't available.

### Pipeline Analytics

`scripts/extract-session-metrics.py` — extracts operational metrics from Claude Code session transcripts to measure agent performance and triage effectiveness.

```bash
# Agent metrics: runtime, tokens, findings, hit rates
python3 scripts/extract-session-metrics.py --limit 50

# Filter to specific agents
python3 scripts/extract-session-metrics.py --agents security-reviewer,pr-reviewer

# Triage effectiveness: dispatch/skip accuracy for adaptive dispatch (Step 3.6)
python3 scripts/extract-session-metrics.py --triage --limit 30
```

Outputs markdown and JSON reports. Auto-detects the Claude Code sessions directory from the current git repo. See `--help` for all options.

## Installation

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
```

## How Reviews Work

1. Check for project-specific context (CLAUDE.md, skills)
2. Check for ground truth files (linters, coverage, scanners)
3. Load skill knowledge (testing-patterns, software-architecture)
4. Analyze code changes
5. Report findings with confidence scores
6. Output both JSON (automation) and Markdown (humans)

Agents use ground truth results at confidence 1.0 and fall back to manual analysis otherwise. All output is dual-format — `.json` for automation, `.md` for reading.

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Changelog](./CHANGELOG.md) | Detailed version history |
| [Guides](./docs/guides/) | User guides and tutorials |
| [False Positive Guide](./docs/guides/FALSE-POSITIVE-HANDLING-GUIDE.md) | Distinguishing real issues from noise |

## Structure

```
pirategoat-tools/
├── agents/           # 19 review agent definitions
├── commands/         # 6 slash commands
├── skills/           # 19 skills with SKILL.md files
│   ├── testing-patterns/references/      # 190KB test quality library
│   └── software-architecture/patterns/   # 87KB design pattern library
├── scripts/          # Helper scripts (review, parsing, linting)
├── hooks/            # Git hook integrations
├── schemas/          # JSON schemas for review output
├── docs/             # Documentation and guides
├── tests/            # Deterministic eval suite
└── CHANGELOG.md
```

## License

MIT — see [LICENSE](../../LICENSE).

# pirategoat-tools

My main Claude Code plugin — the Swiss army knife I reach for on every project. Started as a personal grab bag of experimental features and grew into a proper toolkit for code review, testing, architecture, and WordPress development.

Everything here is opinionated, actively used, and evolving.

## What's Inside

### 17 Review Agents

These run in parallel by default — total review time equals the slowest agent, not the sum of all agents.

| Agent | Focus | Model |
|-------|-------|-------|
| **pr-reviewer** | Generalist — validates changes against stated goals, catches cross-cutting issues | Opus |
| **security-reviewer** | WordPress security — SQL injection, XSS, CSRF, capabilities, sanitization | Sonnet |
| **architecture-reviewer** | Design patterns, SOLID principles, coupling/cohesion (language-agnostic) | Opus |
| **wp-architecture-reviewer** | WordPress-specific — hooks, extensibility, WPCS, backwards compatibility | Opus |
| **performance-reviewer** | N+1 queries, caching, autoloaded options, WP_Query optimization | Sonnet |
| **php-tests-reviewer** | PHPUnit test quality, WordPress factories, WooCommerce patterns | Sonnet |
| **js-tests-reviewer** | Jest/Vitest quality, React Testing Library queries, async patterns | Sonnet |
| **e2e-tests-reviewer** | Playwright quality — locators, Page Object Model, auto-waiting | Sonnet |
| **go-tests-reviewer** | Go testing idioms, table-driven tests, httptest, benchmarks | Haiku |
| **patterns-reviewer** | Codebase archaeology — finds existing patterns, prevents reinventing the wheel | Opus |
| **dead-code-reviewer** | Unused functions, unreachable paths, orphaned imports | Sonnet |
| **history-insights-reviewer** | Mines git history for relevant prior fixes and lessons learned | Opus |
| **tests-mutation-reviewer** | Fault injection to verify tests catch real bugs (runs solo) | Sonnet |
| **gemini-reviewer** | Cross-validates via Google Gemini CLI for independent perspective | Haiku |
| **codex-reviewer** | Cross-validates via OpenAI Codex CLI for independent perspective | Haiku |
| **review-reconciliator** | Aggregates findings from all agents into a single prioritized summary | Sonnet |
| **technical-writer** | Creates documentation after feature completion | Haiku |

#### Model Tiers

Not all review work requires the same level of reasoning. Agents are assigned to model tiers based on what their task actually demands:

- **Opus** (5 agents) — Deep judgment work. The pr-reviewer must understand PR intent and exercise nuanced blocker-vs-preference decisions. Architecture reviewers need structural reasoning about SOLID violations and ecosystem impact. The patterns and history-insights reviewers do genuine codebase archaeology — recognizing analogues across git history, not just keyword matching.
- **Sonnet** (8 agents) — Structured analysis against well-defined checklists. Security tracing follows a source-to-sink framework. Performance detection matches known antipatterns (N+1, unbounded queries). Test reviewers check against catalogued smells. The mutation reviewer follows a rigid 5-phase protocol. All of these benefit from competence but don't need the deep ambiguity-resolution that Opus provides.
- **Haiku** (4 agents) — Orchestration or highly mechanical work. The gemini and codex reviewers just build prompts, shell out to external CLIs, and parse responses. The technical writer fills token-constrained templates. The go-tests-reviewer matches against Go's highly standardized testing idioms — nearly every finding maps to a known pattern.

### 9 Skills

| Skill | What it brings |
|-------|---------------|
| **testing-patterns** | Test quality patterns with a 77KB reference library — philosophy, smells, TDD workflow, PHPUnit/Jest/Playwright |
| **software-architecture** | GoF patterns, SOLID, hexagonal architecture with a 716KB pattern library |
| **wordpress-backend-dev** | WPCS coding standards, security patterns, i18n, hooks API, REST API |
| **pr-reviewing** | Structured PR review workflow with parallel agent spawning |
| **browser-interaction** | Browser automation via MCP servers (chrome-devtools, playwright) |
| **woocommerce-browser-interaction** | WooCommerce-specific browser workflows — login, admin, checkout |
| **dig-into-linear-issue** | Linear issue investigation with RCA templates |
| **creating-md-slides** | Markdown presentations via Marp (PDF, PPTX, HTML) |
| **marp-slide-quality** | SlideGauge integration for presentation analysis |

### 6 Commands

| Command | Purpose |
|---------|---------|
| `/full-code-review` | Run all review agents in parallel on current branch changes |
| `/code-review` | Incremental review of new commits since the last review |
| `/ingest-code-review` | Analyze review findings, filter false positives, propose action plan |
| `/pr-update` | Update PR description with accurate summary of current changes |
| `/fix-github-issue <number>` | Analyze and fix a GitHub issue end-to-end |
| `/execute-plan <plan>` | Execute an implementation plan through delegation and QA |

### Ground Truth Integration

Agents don't guess — they use actual tool outputs when available. Scripts for integrating linters, coverage, and security scanners:

| Phase | Tools | Scripts |
|-------|-------|---------|
| Tests | Jest, PHPUnit, Playwright | `run-tests-for-review.sh` + `parse-test-results.py` |
| Linters | ESLint, PHPCS | `run-linters-for-review.sh` + `parse-linter-results.py` |
| Coverage | Jest, PHPUnit (Xdebug/PCOV) | `run-coverage-for-review.sh` + `parse-coverage-results.py` |
| Security | Semgrep, Bandit | `run-security-scanners-for-review.sh` + `parse-security-results.py` |

All tools are optional — agents fall back to manual analysis when tools aren't available.

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
| [Current Status](./docs/CURRENT-STATUS.md) | What's working now |
| [What's Next](./docs/WHATS-NEXT.md) | Decision guide for next steps |
| [Guides](./docs/guides/) | User guides and tutorials |
| [False Positive Guide](./docs/guides/FALSE-POSITIVE-HANDLING-GUIDE.md) | Distinguishing real issues from noise |

## Structure

```
pirategoat-tools/
├── agents/           # 17 review agent definitions
├── commands/         # 6 slash commands
├── skills/           # 9 skills with SKILL.md files
│   ├── testing-patterns/references/      # 77KB test quality library
│   └── software-architecture/patterns/   # 716KB design pattern library
├── scripts/          # Helper scripts (review, parsing, linting)
├── docs/             # Documentation and guides
├── tests/            # Deterministic eval suite
└── CHANGELOG.md
```

## License

MIT — see [LICENSE](../../LICENSE).

# vladolaru/claude-code-plugins

My personal Claude Code plugins marketplace featuring specialized skills, commands, and agents for development workflows.

**Featured:** Comprehensive testing and architecture skills with 793KB of deep-dive pattern references synthesized from software architecture best practices.

## Quick Start

```bash
# Add marketplace
/plugin marketplace add vladolaru/claude-code-plugins

# Install plugins
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install dex@vladolaru-claude-code-plugins

# Restart Claude Code to activate
```

## Installation

### Add the Marketplace

```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugins

Choose the plugins you need:

```bash
# Main plugin (recommended) - includes testing-patterns, software-architecture, and review agents
/plugin install pirategoat-tools@vladolaru-claude-code-plugins

# Image optimization
/plugin install image-optimizer@vladolaru-claude-code-plugins

# Prompt engineering
/plugin install prompt-engineer@vladolaru-claude-code-plugins

# Knowledge capture (compound engineering)
/plugin install dex@vladolaru-claude-code-plugins
```

Then restart Claude Code to activate the plugins.

## Plugins

### pirategoat-tools (v1.10.0)

Vlad Olaru's personal public Claude Code tools - experimental features that may eventually be extracted into standalone plugins.

**Includes:** 11 review agents, 9 skills, rich feedback loop integration (linters, coverage, security scanners)

**[Full Documentation →](plugins/pirategoat-tools/README.md)** | [CHANGELOG](plugins/pirategoat-tools/CHANGELOG.md)

#### Skills

| Skill | Description |
|-------|-------------|
| **wordpress-backend-dev** | WordPress plugin/theme PHP development - WPCS coding standards, security patterns, i18n, hooks API, REST API |
| **browser-interaction** | Browser automation for debugging, verification, testing using MCP servers (chrome-devtools, playwright, puppeteer) |
| **woocommerce-browser-interaction** | WooCommerce-specific browser automation patterns (login, admin, frontend, block checkout) |
| **dig-into-linear-issue** | Thorough Linear issue investigation workflow with RCA templates and validation paths |
| **pr-reviewing** | Structured PR review workflow ensuring context gathering before code review |
| **creating-md-slides** | Create presentation slides from Markdown using Marp (PDF, PPTX, HTML output) |
| **marp-slide-quality** | Analyze and improve Marp presentations using SlideGauge quality checks |
| **testing-patterns** | Comprehensive test quality patterns for PHP (PHPUnit/WordPress), JavaScript (Jest/Vitest), and E2E (Playwright) with 77KB reference library covering test philosophy, quality principles, smells, TDD workflow, and test layers |
| **software-architecture** | Design patterns, SOLID principles, hexagonal architecture, and composable design guidance with 716KB pattern reference library covering GoF patterns, architectural principles, and refactoring strategies |

#### Commands

| Command | Description |
|---------|-------------|
| `/fix-github-issue <number>` | Analyze and fix a GitHub issue end-to-end |
| `/execute-plan <plan>` | Execute an implementation plan through delegation and quality assurance |

#### Agents

Specialized review agents for the Task tool:

| Agent | Description |
|-------|-------------|
| **pr-reviewer** | Generalist PR reviewer - validates code changes against stated goals, identifies critical issues across all categories |
| **architecture-reviewer** | Software architecture review for design patterns, SOLID principles, coupling/cohesion, architectural code smells (works with any language) |
| **tests-reviewer** | Test quality review for test structure, assertions, mocking patterns, coverage, and anti-patterns across PHPUnit, Jest/Vitest, and Playwright |
| **security-reviewer** | WordPress security review (XSS, SQL injection, CSRF/nonces, capabilities, sanitization/escaping) |
| **performance-reviewer** | WordPress performance review (N+1 queries, caching/transients, autoloaded options, WP_Query) |
| **wp-architecture-reviewer** | WordPress-specific architecture review (hooks/extensibility, WPCS, backwards compatibility, i18n) |
| **patterns-reviewer** | Explores codebase and git history for existing patterns, ensures consistency, prevents reinventing the wheel |
| **gemini-reviewer** | Cross-validates PR changes using Google Gemini CLI for independent perspective |
| **codex-reviewer** | Cross-validates PR changes using OpenAI Codex CLI for independent perspective |
| **review-reconciliator** | Reads all review files, reconciles findings, produces consolidated summary |
| **technical-writer** | Creates documentation after feature completion |

---

### image-optimizer (v1.1.0)

Lossless image optimization (PNG, JPEG, GIF, SVG) with review/confirm workflow.

**Includes:** 1 command (`/optimize-images`), optimization scripts

**[Full Documentation →](plugins/image-optimizer/README.md)** | [CHANGELOG](plugins/image-optimizer/CHANGELOG.md)

---

### prompt-engineer (v2.0.0)

Human-in-the-loop prompt optimization with evidence-grounded pattern attribution.

**Includes:** 1 skill, 1 command (`/optimize-prompt`), comprehensive reference library

**[Full Documentation →](plugins/prompt-engineer/README.md)** | [CHANGELOG](plugins/prompt-engineer/CHANGELOG.md)

---

### dex (v1.0.1)

One-click knowledge capture that compounds engineering work. Based on [compound engineering](https://every.to/guides/compound-engineering) — each unit of work should make the next one easier, not harder.

**Includes:** 1 skill, 5 commands (`/dex`, `/dex:learn`, `/dex:pattern`, `/dex:init`, `/dex:status`)

| Command | Purpose |
|---------|---------|
| `/dex` | Auto-classifies knowledge (learning, pattern, or decision) and routes to the right handler |
| `/dex:learn` | Captures a learning — discovery, fix, gotcha, debugging insight |
| `/dex:pattern` | Captures a reusable pattern — approach, convention, anti-pattern |
| `/dex:init` | Scaffolds `.claude/docs/` knowledge infrastructure for a new project |
| `/dex:status` | Shows knowledge health report — CLAUDE.md budget, doc counts, latest entries |

**[Full Documentation →](plugins/dex/README.md)** | [CHANGELOG](plugins/dex/CHANGELOG.md)

---

## Repository Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── plugins/
│   ├── pirategoat-tools/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── agents/               # Review agents (11 agents)
│   │   ├── commands/             # Slash commands (2 commands)
│   │   └── skills/               # Skills (9 skills)
│   │       ├── testing-patterns/
│   │       │   └── references/   # 77KB test quality reference library
│   │       └── software-architecture/
│   │           └── patterns/     # 716KB design pattern reference library
│   ├── image-optimizer/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── commands/             # Slash commands
│   │   └── scripts/              # Optimization scripts
│   ├── prompt-engineer/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── commands/             # Slash commands
│   │   └── skills/               # Skills with reference docs
│   └── dex/
│       ├── CHANGELOG.md          # Version history
│       ├── commands/             # 5 slash commands
│       ├── skills/               # Knowledge capture skill
│       └── tests/                # Structural validation tests
├── CLAUDE.md                     # Development instructions
├── LICENSE
└── README.md
```

## Development

To test changes locally:

```bash
# Clone the repo
git clone https://github.com/vladolaru/claude-code-plugins.git
cd claude-code-plugins

# Add as local marketplace
/plugin marketplace add /path/to/claude-code-plugins

# Install from local
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
/plugin install prompt-engineer@vladolaru-claude-code-plugins
/plugin install dex@vladolaru-claude-code-plugins
```

## Highlights

### 🧪 Testing-Patterns Skill (77KB Reference Library)

Comprehensive test quality guidance for writing and reviewing high-quality tests:

| Feature | Coverage |
|---------|----------|
| **Core Philosophy** | Tests as specifications (not verification), behavior vs implementation, future-focused testing |
| **Quality Principles** | 9 attributes: Behavior-based, Independent, Deterministic, Fast, Readable, Single Concern, Declarative, Complete, Maintainable |
| **Test Smells** | Diagnostic guide for 6 major smells: Flaky (reveals implementation bugs!), Brittle, Slow, Complex, False Positive, Over-Mocked |
| **TDD Workflow** | Complete Red-Green-Refactor cycle, Three Laws of TDD, Test && Commit \|\| Revert |
| **Test Layers** | Unit/Integration/System with Mars Orbiter lesson, Pyramid/Trophy/Ice Cream Cone strategies |
| **Test Benefits** | 13 benefits from specifications to future bug prevention |
| **Frameworks** | PHPUnit/WordPress, Jest/Vitest/React Testing Library, Playwright E2E |
| **References** | 11 deep-dive documents with real-world examples, quotes, and further reading |

**Synthesized from:** [jhumelsine.github.io](https://jhumelsine.github.io) testing series

### 🏗️ Software-Architecture Skill (716KB Pattern Library)

Comprehensive design patterns and SOLID principles for maintainable systems:

| Feature | Coverage |
|---------|----------|
| **Essential Patterns** | DEMS D'FFACTS: Command, Strategy, Template Method, Adapter, Façade, Factory, Dependency Injection |
| **Pattern Categories** | 5 Behavioral, 5 Structural, 2 Creational, 1 Architectural (Hexagonal) |
| **SOLID Principles** | Complete guide (47KB) with violation symptoms, fixes, and pattern support mapping |
| **Pattern Selection** | Decision matrices mapping architectural problems to pattern solutions |
| **Composable Design** | Composition over inheritance, emergent behavior, pattern progression (Proxy→Decorator→Chain→Composite→Specification→Interpreter) |
| **Hexagonal Architecture** | Ports & Adapters, Clean Architecture, Dependency & Knowledge Management (70KB from 5 blog posts) |
| **Anti-Patterns** | Over-engineering detection, pattern abuse, premature abstraction (Rule of Three, YAGNI) |
| **Code Examples** | All implementations in PHP (adaptable to JavaScript/TypeScript/other OOP languages) |
| **References** | 17 deep-dive documents with structure, benefits, trade-offs, common mistakes, decision criteria |

**Synthesized from:** [jhumelsine.github.io](https://jhumelsine.github.io) architecture series

### 🔍 Comprehensive Review Agents

Specialized agents that leverage the skills above with production-tested quality:

| Agent | Leverages Skill | Detection Rate | Output Quality |
|-------|-----------------|----------------|----------------|
| **architecture-reviewer** | software-architecture | 100% (18/18 issues) | 35KB structured review with SOLID violations, pattern opportunities, refactoring roadmap |
| **tests-reviewer** | testing-patterns | 100% (14/14 issues) | 23KB review with false confidence detection, flaky test root cause analysis |
| **security-reviewer** | WordPress security patterns | 100% (15/15 vulns) | 35KB with CVSS scores, exploitation examples, remediation code |
| **performance-reviewer** | WordPress performance patterns | 100% (14/14 issues) | 20KB with 10x/100x scale analysis, caching strategies |
| **pr-reviewer** | All skills | Comprehensive | Generalist overview with goal alignment, prioritized recommendations |

**All agents tested** with intentional anti-patterns achieving 100% detection accuracy.

## Credits

The following components were adapted from [solatis/claude-config](https://github.com/solatis/claude-config):
- **prompt-engineer** skill and reference documents

The following components were adapted from [zl190/md-slides](https://github.com/zl190/md-slides):
- **creating-md-slides** skill
- **marp-slide-quality** skill

The following components synthesize insights from [jhumelsine.github.io](https://jhumelsine.github.io):
- **testing-patterns** skill and comprehensive reference library (77KB)
- **software-architecture** skill and design pattern library (716KB)
- Source: Jim Humelsine's excellent software architecture and testing blog series

## Resources

- [Claude Code Documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Plugins Guide](https://docs.anthropic.com/en/docs/claude-code/plugins)
- [Skills Guide](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)
- [Slash Commands Reference](https://docs.anthropic.com/en/docs/claude-code/slash-commands)

## License

MIT License - see [LICENSE](LICENSE) file for details.

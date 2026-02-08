# pirategoat-tools

Vlad Olaru's personal Claude Code development tools - experimental features for WordPress development, code review, and workflow automation.

**Current Version:** 1.10.0

---

## What's Included

### 13 Review Agents
Specialized agents for comprehensive code review:
- **security-reviewer** - WordPress security (SQL injection, XSS, CSRF, capabilities)
- **architecture-reviewer** - Design patterns, SOLID principles, coupling/cohesion
- **performance-reviewer** - N+1 queries, caching, autoload, WP_Query optimization
- **php-tests-reviewer** - PHP/PHPUnit test quality, WordPress factories, WooCommerce patterns
- **js-tests-reviewer** - Jest/Vitest test quality, RTL queries, async patterns, snapshot discipline
- **e2e-tests-reviewer** - Playwright E2E test quality, locators, Page Object Model
- **patterns-reviewer** - Codebase archaeology, existing patterns, consolidation
- **wp-architecture-reviewer** - WordPress hooks, WPCS, extensibility, backwards compatibility
- **pr-reviewer** - Generalist orchestrator for all reviews
- **review-reconciliator** - Aggregates findings from all agents
- **gemini-reviewer** - External AI cross-validation (Google Gemini)
- **codex-reviewer** - External AI cross-validation (OpenAI Codex)
- **technical-writer** - Post-implementation documentation

### 9 Skills
Specialized knowledge and workflows:
- **pr-reviewing** - Complete PR review workflow with parallel agent spawning
- **testing-patterns** - Test quality patterns (77KB reference library)
- **software-architecture** - GoF patterns, SOLID, hexagonal architecture (716KB reference)
- **wordpress-backend-dev** - WordPress/PHP development patterns
- **browser-interaction** - Browser automation for debugging and testing
- **woocommerce-browser-interaction** - WooCommerce-specific browser workflows
- **dig-into-linear-issue** - Linear issue investigation and fixing
- **creating-md-slides** - Marp/Beamer/reveal.js presentation creation
- **marp-slide-quality** - SlideGauge integration for presentation analysis

### Rich Feedback Loops (Ground Truth Integration)
Scripts for integrating tool outputs into reviews:

**Phase 1: Test Results**
- `run-tests-for-review.sh` - Execute Jest, PHPUnit, Playwright
- `parse-test-results.py` - Unify test results across frameworks

**Phase 2: Linters**
- `run-linters-for-review.sh` - Execute ESLint and PHPCS
- `parse-linter-results.py` - Unify linter violations

**Phase 3: Coverage**
- `run-coverage-for-review.sh` - Generate coverage reports
- `parse-coverage-results.py` - Unify Jest and PHPUnit coverage

**Phase 4: Security Scanners**
- `run-security-scanners-for-review.sh` - Execute Semgrep and Bandit
- `parse-security-results.py` - Unify security findings

**Supporting:**
- `review_output_simple.py` - JSON + Markdown output builder
- `semantic-filter.py` - 40% noise reduction for diffs

### 2 Commands
- **/execute-plan** - Execute implementation plans with checkpoints
- **/fix-github-issue** - Analyze and fix GitHub issues by number/URL

---

## Installation

### Add Marketplace
```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugin
```bash
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
```

---

## Quick Start

### PR Review with All Capabilities
```bash
# Use the pr-reviewing skill
# It automatically spawns agents in parallel and integrates ground truth

# Optional: Enable verbose reasoning
export VERBOSE=true

# Optional: Run tools first for ground truth
cd /path/to/project
./path/to/run-linters-for-review.sh /tmp/review
./path/to/run-coverage-for-review.sh /tmp/review
./path/to/run-security-scanners-for-review.sh /tmp/review

# Parse results
./path/to/parse-linter-results.py /tmp/review/*.json > /tmp/review/lint-unified.json
./path/to/parse-coverage-results.py /tmp/review/ > /tmp/review/coverage-unified.json
./path/to/parse-security-results.py /tmp/review/ > /tmp/review/security-unified.json

# Then run PR review - agents will automatically use the ground truth
```

### Use Individual Agents
```bash
# Security review only
# security-reviewer will check for ground truth files

# Architecture review only
# architecture-reviewer will check for linter results

# Test quality review (language-specific agents)
# php-tests-reviewer, js-tests-reviewer, e2e-tests-reviewer check for test results and coverage
```

---

## Key Features

### Parallel Agent Spawning (3.3x Faster)
Reviews run in parallel by default - total time = max(any agent), not sum(all agents).

### Verbose Reasoning Mode
Set `VERBOSE=true` to see agent reasoning:
- Detection process
- Confidence scores
- Alternative interpretations
- Skill/pattern references

### Ground Truth Integration
Agents use actual tool outputs (not guessing):
- Linters → definitive coding standards violations
- Coverage → exact untested lines
- Security scanners → confirmed vulnerability patterns
- Test results → actual pass/fail status

### Structured Output (JSON + Markdown)
All agents output both:
- `.json` - Machine-readable for automation
- `.md` - Human-readable with reasoning

### False Positive Handling
Comprehensive guidance for distinguishing real issues from false positives.
See: `docs/guides/FALSE-POSITIVE-HANDLING-GUIDE.md`

---

## Documentation

**Main Documentation:** `docs/README.md`

**Quick Links:**
- [Current Status](./docs/CURRENT-STATUS.md) - What's working (v1.10.0)
- [What's Next](./docs/WHATS-NEXT.md) - Decision guide for next steps
- [Guides](./docs/guides/) - User guides and tutorials
- [Research](./docs/research/) - Agentic patterns analysis
- [False Positive Guide](./docs/guides/FALSE-POSITIVE-HANDLING-GUIDE.md)

---

## Requirements

### Optional Tools (for ground truth)
- **ESLint** - JavaScript/TypeScript linting
- **PHPCS** - PHP linting (WordPress-Extra standard recommended)
- **Semgrep** - Security scanning (`brew install semgrep`)
- **Jest** - JavaScript testing with coverage
- **PHPUnit** - PHP testing with coverage (needs Xdebug or PCOV)
- **Playwright** - E2E testing

**Note:** All tools are optional - agents work without them (manual analysis mode).

---

## Architecture

### Agent Philosophy
- Load skill knowledge before reviewing
- Check for ground truth tool outputs
- Use tool results when available (confidence = 1.0)
- Fall back to manual analysis when tools unavailable
- Output both JSON (automation) and Markdown (humans)

### Review Workflow
1. Check for project-specific knowledge (CLAUDE.md, skills)
2. Check for ground truth files (linters, coverage, scanners)
3. Load skill knowledge (testing-patterns, software-architecture)
4. Analyze code changes
5. Report findings with confidence scores
6. Generate dual outputs (JSON + Markdown)

---

## Contributing

This plugin is experimental and evolving. Structure:
- `agents/` - Agent definitions
- `skills/` - Skill definitions with SKILL.md
- `commands/` - Slash command definitions
- `scripts/` - Helper scripts
- `docs/` - All documentation
- `schemas/` - TypeScript type definitions
- `test-samples/` - Test data for validation

---

## License

MIT License - See [LICENSE](../../LICENSE)

---

## Author

**Vlad Olaru** - [@vladolaru](https://github.com/vladolaru)

**Repository:** https://github.com/vladolaru/claude-code-plugins

---

## Version History

See [CHANGELOG.md](./CHANGELOG.md) for detailed version history.

**Latest:** v1.10.0 - Rich Feedback Loops Phases 2-4 complete (Linters, Coverage, Security Scanners)

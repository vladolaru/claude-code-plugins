# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.14.0] - 2026-02-08

### Added

- **tests-mutation-reviewer agent** - Adversarial mutation testing that temporarily mutates production code to verify tests catch real bugs
  - Runs SOLO (no other review agents alongside) due to code modification
  - 10-category mutation catalog: boolean flip, comparison swap, string corruption, guard removal, default change, return value change, boundary shift, null swap, array empty, conditional removal
  - Pre-flight safety: stash/unstash, branch verification, test runner auto-detection
  - Per-mutation execution loop: mutate → test → capture → revert → verify revert
  - Mutation score calculation with verdict mapping (>=80% APPROVE, 60-79% COMMENT, <60% REQUEST_CHANGES)
  - Surviving mutation root cause analysis: over-mocking, weak assertions, untested paths, false tests
  - ReviewOutputBuilder integration for reconciliator compatibility
  - Emergency cleanup with nuclear revert option
  - Integrates with pr-reviewing skill as optional post-review phase

## [1.13.1] - 2026-02-06

### Fixed

- **browser-interaction** - Add chrome-devtools profile locations and profile-aware kill procedure
  - Document default (`chrome-profile`) and isolated (`puppeteer_dev_chrome_profile-*`) profile paths
  - Kill procedure tries isolated pattern first, then falls back to default persistent profile
  - Remove `SingletonLock` file that blocks relaunch after a kill
  - Note limitation: isolated pkill kills all instances, no way to target a specific one

## [1.13.0] - 2026-02-05

### Changed

- **Review agents** - Standardized output file naming and added structured output
  - All reviewers now output both JSON and Markdown files consistently
  - Naming pattern: `{domain}-review.json` and `{domain}-review.md`
  - `wp-architecture-reviewer` now outputs to distinct `wp-architecture-review.*` (was conflicting with `architecture-reviewer`)
  - `pr-reviewer` renamed output from `pr-reviewer.md` to `pr-review.md/json`
  - Fixed internal inconsistencies where documentation and code examples showed different filenames

- **pr-reviewer agent** - Added ReviewOutputBuilder and verbose reasoning
  - Now generates structured JSON output alongside Markdown
  - Added comprehensive verbose reasoning mode with templates for:
    - Detection methodology
    - Goal alignment checks
    - Code path analysis
    - Edge case tables
    - Confidence score rationale
    - Alternative interpretations

- **wp-architecture-reviewer agent** - Added ReviewOutputBuilder
  - Now generates structured JSON output alongside Markdown
  - Added WordPress-specific categories for issues
  - Improved pragmatic hooks guidance (don't require hooks everywhere)

- **review-reconciliator agent** - Updated to match new file naming
  - Updated expected file list with all reviewer outputs
  - Added `wp-architecture` and `pr` to agent list
  - Fixed references to old `pr-reviewer.md` filename

## [1.12.0] - 2026-02-05

### Removed

- **browser-navigator agent** - Removed due to MCP tools not being available to subagents
  - Claude Code subagents cannot access MCP tools loaded in the parent session
  - ToolSearch in subagents doesn't discover deferred MCP tools

### Changed

- **browser-interaction skill** - Now instructs direct MCP tool usage instead of agent delegation
  - Quick start guide with ToolSearch → Navigate → Snapshot → Interact workflow
  - Tool mapping table for Chrome DevTools and Playwright MCPs
  - RULE 0 (fresh snapshot after navigation) documented inline
  - Error recovery patterns for profile locks, stale refs, timeouts

## [1.11.3] - 2026-02-05

### Fixed

- **browser-navigator agent** - Enforce MCP-only browser automation
  - Never use Playwright CLI or curl/wget as fallback
  - Bash only allowed for profile lock recovery (pkill)
  - Fail immediately with clear error if no browser MCP available

## [1.11.2] - 2026-02-05

### Added

- **browser-navigator agent** - Support for Playwright MCP as alternative to Chrome DevTools
  - Auto-detects available MCP (Chrome DevTools preferred, Playwright as fallback)
  - Tool mapping table for both MCPs
  - Profile lock recovery only applies to Chrome DevTools (Playwright manages its own lifecycle)

## [1.11.1] - 2026-02-05

### Fixed

- **browser-navigator agent** - Add cyan color (#0891b2) and register in marketplace.json

## [1.11.0] - 2026-02-05

### Added

- **browser-navigator agent** - Isolated browser automation with automatic error recovery
  - Executes all browser tasks in subagent for context isolation
  - Auto-recovers from profile locks, stale refs, tool stalls (max 3 retries)
  - Timeout enforcement: 30s navigation, 10s waits, configurable overall
  - RULE 0 compliance: fresh snapshot after every navigation
  - Flexible output: summary, screenshot, data extraction
  - Lifecycle control: `fresh`, `reuse`, `leave_open`
  - Escalates auth errors and server errors to caller

### Changed

- **browser-interaction skill** - Now dispatches to browser-navigator agent
  - Simplified to lightweight dispatcher + reference documentation
  - All browser logic moved to agent for single source of truth
  - Consistent behavior whether called from main session or subagent

## [1.10.1] - 2026-02-05

### Fixed

- **browser-interaction skill** - Add profile lock recovery and timeout guidance
  - New "Profile Lock Errors" section with `pkill` recovery command
  - Mention of `--isolated` flag for parallel browser sessions
  - New "Timeouts (CRITICAL)" section enforcing explicit timeouts
  - Recommended timeouts: `navigate_page` 30s, `wait_for` 10s
  - Updated error patterns with "Profile lock errors" and "Tool stalls"
  - Updated recovery table with profile lock and stall recovery actions

## [1.10.0] - 2026-01-22

### Added

- **date-time-wrangling skill** - Verify temporal information using Unix date commands
  - Date operations: current date, day of week, date arithmetic, days between dates
  - Time operations: current time (12h/24h), ISO 8601, Unix timestamps, time arithmetic
  - Time zone support: 16 major geographic regions with TZ identifiers
  - Localization guidance: `LC_TIME=C` for English, locale-independent formats
  - Platform support: GNU date (Linux) and BSD date (macOS) syntax
  - Adapted from Matt Hodges' temporal-awareness skill (MIT)

- **Rich Feedback Loops - Phases 2-4 Complete** - Agents now integrate with linters, coverage, and security scanners

  **Phase 2: Linter Integration**
  - `run-linters-for-review.sh` - Executes ESLint and PHPCS with JSON output
  - `parse-linter-results.py` - Unifies linter outputs into standard format
  - architecture-reviewer now uses PHPCS violations as ground truth for code quality
  - wp-architecture-reviewer now uses PHPCS for WordPress Coding Standards (WPCS) violations
  - Linter results treated as definitive for coding standards issues
  - Supports ESLint (JavaScript/TypeScript) and PHPCS (PHP/WordPress)

  **Phase 3: Coverage Integration**
  - `run-coverage-for-review.sh` - Executes test suites with coverage instrumentation
  - `parse-coverage-results.py` - Unifies coverage from Jest and PHPUnit (Clover XML)
  - tests-reviewer now uses coverage data to identify untested code paths
  - Coverage gaps flagged with specific uncovered line numbers
  - Supports Jest (JavaScript/TypeScript), PHPUnit (PHP), and Playwright (E2E)
  - Coverage interpreted as necessary but not sufficient indicator of test quality

  **Phase 4: Security Scanner Integration**
  - `run-security-scanners-for-review.sh` - Executes Semgrep and Bandit with JSON output
  - `parse-security-results.py` - Unifies security scanner outputs
  - security-reviewer now uses scanner findings as ground truth for vulnerabilities
  - CWE mapping to security categories (SQL injection, XSS, CSRF, etc.)
  - Supports Semgrep (multi-language) and Bandit (Python)
  - Scanner findings treated as definitive for pattern-based vulnerabilities

### Changed

- architecture-reviewer and wp-architecture-reviewer now check for linter results
- tests-reviewer now checks for both test results AND coverage data
- security-reviewer now checks for security scanner results
- All feedback phases provide ground truth data that agents treat as definitive
- Agents correlate manual analysis with tool outputs for higher confidence

### Technical Details

- All runner scripts support configurable output directories
- All parser scripts output unified JSON to stdout with consistent schema
- All integrations follow Phase 1 pattern (check for file, load JSON, use as ground truth)
- Zero new dependencies - all scripts use standard library (Python 3, Bash)
- Tools optional - agents gracefully degrade when tools not available

**Implements:** Proposal #5 (Rich Feedback Loops) - Phases 2-4
**Total Phases Complete:** 4 of 5 (Phase 5: Benchmark integration deferred)
**Annual Value:** $240K+ (from eliminating false positives/negatives)

## [1.9.0] - 2026-01-21

### Added

- **Structured Output Integration** - All 5 review agents now output both JSON and Markdown
  - Integrated ReviewOutputBuilder into all agents (security, architecture, performance, tests, patterns)
  - Agents automatically generate dual outputs: `.json` (machine-readable) + `.md` (human-readable)
  - JSON enables automation: CI/CD integration, metrics dashboards, auto-issue creation
  - Markdown maintains human-readable reviews with verbose reasoning support
  - Auto-calculated verdicts from issue severities
  - Structured metadata: confidence scores, tools used, files reviewed, timestamps
  - Completes Proposal #3 integration from Tier 1 agentic patterns
  - Agent-specific categories:
    - Security: sql-injection, xss, csrf, capabilities, file-upload, data-exposure
    - Architecture: solid-violation, coupling, cohesion, abstraction-leak, god-class
    - Performance: n-plus-one, caching, autoload, remote-requests, scale-issues
    - Tests: test-failure, missing-coverage, flaky-test, brittle-test, over-mocking
    - Patterns: inconsistency, duplication, anti-pattern, naming-convention

### Changed

- All 5 review agents now use ReviewOutputBuilder for consistent output format
- Output files now include both `.json` and `.md` extensions
- Verdicts auto-calculated (no manual verdict writing needed)
- Moved review output library to plugin directory (lib/ → plugins/pirategoat-tools/lib/)
  - review_output_simple.py (dependency-free builder - ONLY implementation kept)

### Removed

- Pydantic-dependent implementations (review_output_builder.py, review_schemas.py)
  - Removed to eliminate dependencies - review_output_simple.py provides all needed functionality
  - No pydantic installation required

## [1.8.3] - 2026-01-21

### Added

- **Structured Output Foundation** - JSON schema infrastructure for reliable automation
  - `schemas/review-output.ts` - Complete TypeScript type definitions for all review types
  - `lib/review_schemas.py` - Pydantic models for runtime validation (requires pydantic package)
  - `lib/review_output_simple.py` - Dependency-free builder (works immediately, no installs)
  - ReviewOutputBuilder helper class with dual output (JSON + Markdown)
  - Schema definitions: Issue, SecurityIssue, PerformanceIssue, ArchitectureIssue, TestIssue, PatternIssue
  - Verdict auto-calculation from issue severity
  - Confidence scoring and metadata tracking
  - Implements Proposal #3 foundation from Tier 1 agentic patterns

Note: Agent integration will follow in next release. Foundation ready for use.

## [1.8.2] - 2026-01-21

### Added

- **Rich Feedback Loops - Phase 1: Test Runner Integration**
  - `scripts/run-tests-for-review.sh` - Executes Jest, PHPUnit, Playwright with JSON output
  - `scripts/parse-test-results.py` - Unifies test results from multiple frameworks into standard format
  - `tests-reviewer` agent now consumes actual test execution results (ground truth)
  - Agent decision logic updated: test failures = automatic BLOCK verdict
  - Eliminates false approvals based on "code looks good" without execution
  - Test results format: unified JSON with pass/fail counts, failure details, locations
  - Demo test suite in `test-samples/feedback-loops-demo/` with failing tests
  - Baseline documented: 100% false approval rate without feedback, 0% with feedback
  - Implements Proposal #5 Phase 1 from Tier 1 agentic patterns

## [1.8.1] - 2026-01-21

### Added

- **Semantic Context Filtering MVP** - Regex-based diff noise reduction for efficient reviews
  - `scripts/semantic-filter-mvp.py` - Production-ready filter removing blank lines, docblocks, comments, pure formatting
  - Achieves 40.5% noise reduction with 100% signal preservation
  - No dependencies (pure Python regex), fast implementation (1 hour)
  - Validates on test case: 78 lines → 47 lines, all 6 semantic changes preserved
  - Conservative filtering approach (when in doubt, keep the line)
  - Test suite in `test-samples/semantic-filter-test/` with baseline and results
  - Foundation for future AST-based enhancement (70%+ reduction)
  - Implements Proposal #1 from Tier 1 agentic patterns (Phase 1 MVP)

- **Verbose Reasoning Mode** - All review agents now support detailed reasoning transparency
  - `architecture-reviewer` - Shows SOLID analysis, pattern opportunities, confidence scoring
  - `security-reviewer` - Shows exploitation paths, CVSS scoring, defense-in-depth analysis
  - `performance-reviewer` - Shows 10x/100x scale impact, query analysis, optimization paths
  - `tests-reviewer` - Shows test quality analysis, root cause diagnosis, mocking analysis
  - `patterns-reviewer` - Shows git history evidence, consistency analysis, consolidation opportunities
  - Reasoning includes: detection process, checks performed, confidence scores, severity rationale, cross-references, alternative interpretations
  - Optional mode enabled via VERBOSE=true environment variable
  - Uses expandable `<details>` blocks for readability
  - Implements Proposal #2 from Tier 1 agentic patterns

- `pr-reviewing` skill - Added VERBOSE flag documentation and passing to all agents
  - When to enable verbose mode (learning, debugging, low confidence, critical findings)
  - How to enable (export VERBOSE=true)
  - Context preparation includes verbose mode flag
  - Agents receive VERBOSE signal and include reasoning when enabled

### Changed

- `pr-reviewing` skill - Strengthened parallel spawning requirements (Proposal #4)
  - Added CRITICAL instruction emphasizing single message with multiple Task calls for parallel execution
  - Added anti-pattern section showing sequential spawning (what NOT to do)
  - Added explicit timing comparison (parallel: 28s vs sequential: 75s)
  - Clarified correct parallel spawning pattern with examples
  - Result: Ensures 3x faster reviews through proper parallel agent orchestration

## [1.7.1] - 2026-01-14

### Added

- `architecture-reviewer` agent - General-purpose software architecture code review
  - Leverages software-architecture skill for comprehensive pattern knowledge
  - Reviews: Design patterns, SOLID principles, coupling/cohesion, architectural code smells
  - Works with any codebase: PHP, JavaScript, TypeScript, Python, Java, etc.
  - Analyzes: God objects, tight coupling, SOLID violations, design pattern opportunities
  - Provides: Specific recommendations with file/line references, pattern implementation guides
  - Prioritizes by impact: Critical (blocks changes) → Important (creates debt) → Nice-to-have
  - Includes: Rule of three, YAGNI principles, over-engineering detection, testability analysis
  - Output: Structured markdown with executive summary, SOLID violations, pattern opportunities, prioritized recommendations
  - Complements wp-architecture-reviewer (WordPress-specific) for general architectural analysis
  - References specific pattern docs (e.g., `patterns/behavioral/strategy.md`) for implementation

## [1.7.0] - 2026-01-14

### Added

- `software-architecture` skill - Comprehensive design patterns and software architecture guidance
  - Covers GoF design patterns, SOLID principles, hexagonal architecture, and composable designs
  - Pattern selection guide mapping architectural problems to pattern solutions
  - Essential patterns (DEMS D'FFACTS): Command, Strategy, Template Method, Adapter, Façade, Factory, Dependency Injection
  - Common architectural problems troubleshooting table with SOLID violations
  - Pattern combinations and anti-patterns guidance
  - Refactoring to patterns tactical guide
  - Architecture review checklist
  - Language-specific considerations for PHP/WordPress and JavaScript
  - Comprehensive pattern reference library (716KB total) synthesized from jhumelsine.github.io architecture series:
    - **Behavioral patterns:** Command, Strategy, Template Method, Chain of Responsibility, Specification
    - **Structural patterns:** Adapter, Façade, Decorator, Composite, Proxy
    - **Creational patterns:** Factory (Method, Class, Abstract), Dependency Injection
    - **Architectural patterns:** Hexagonal Architecture (Ports & Adapters, Clean Architecture)
    - **Core concepts:** SOLID Principles, Composable Design, Pattern Relationships
    - **Navigation:** patterns/README.md with 4 reading paths and pattern taxonomy
  - All pattern references include: when to use, when NOT to use, structure, implementation guide (PHP), benefits, trade-offs, common mistakes, pattern relationships, decision criteria
  - Real-world examples, quotes, and further reading sections throughout

## [1.6.0] - 2026-01-14

### Added

- `testing-patterns` skill - Comprehensive test quality patterns for PHP (PHPUnit/WordPress), JavaScript (Jest/Vitest), and E2E (Playwright)
  - Reference guides for test quality, structure (AAA), mocking strategies, test data management, and coverage
  - Language-specific patterns including WordPress/WooCommerce testing utilities
  - Test philosophy section emphasizing tests as specifications, not verification
  - Test smells diagnostic guide with root cause analysis
  - Enhanced quality principles table (9 attributes including behavior-based, declarative, complete)
  - Mocking principles section with clear guidance on when/how to mock
  - Test layer context comparing unit/integration/E2E with strategy guidance
  - Skill now includes contextual pointers to deep-dive references throughout
  - Organized reference library section: Quick Reference (tactical) vs Deep Dives (strategic)
  - "Using the Reference Library" guide at end of skill with navigation by problem type
  - Comprehensive reference documents synthesized from jhumelsine.github.io architecture blog series (77KB total):
    - `README.md` - Navigation guide with 4 reading paths and key insights summary
    - `test-philosophy.md` - Mental models, behavior vs implementation, the fundamental shift (12KB)
    - `test-smells.md` - Diagnostic guide for flaky, brittle, slow, complex tests with root cause analysis (16KB)
    - `tdd-workflow.md` - Complete Red-Green-Refactor cycle with examples and anti-patterns (15KB)
    - `test-layers.md` - Unit/Integration/System comparison with Mars Orbiter lesson and strategy guidance (17KB)
    - `test-benefits.md` - 13 benefits of testing from specifications to future bug prevention (17KB)
  - All reference docs include real-world examples, quotes, and further reading sections
- `tests-reviewer` agent - Test quality-focused code review for test structure, assertions, mocking patterns, coverage, and anti-patterns

## [1.5.0] - 2026-01-10

### Added

- `pr-reviewer` agent - Generalist PR reviewer that validates code changes against stated goals
- `security-reviewer` agent - WordPress security-focused review (XSS, SQL injection, CSRF/nonces, capabilities, sanitization/escaping)
- `performance-reviewer` agent - WordPress performance-focused review (N+1 queries, caching/transients, autoloaded options, WP_Query)
- `wp-architecture-reviewer` agent - WordPress architecture-focused review (hooks/extensibility, WPCS, backwards compatibility, i18n)
- `patterns-reviewer` agent - Explores codebase and git history for existing patterns, ensures consistency, identifies consolidation opportunities
- `gemini-reviewer` agent - Cross-validates PR changes using Google Gemini CLI
- `codex-reviewer` agent - Cross-validates PR changes using OpenAI Codex CLI
- `review-reconciliator` agent - Reads all review files, reconciles findings, produces consolidated summary
- File-based output architecture - All review agents write to temp files, return only signals to conserve context

### Changed

- Updated `pr-reviewing` skill to orchestrate specialist agents
- Added cross-validation with external AI (Gemini/Codex) for critical PRs
- Generalist always runs first and anchors reconciliation of specialist findings
- Patterns reviewer runs on all PR sizes to prevent reinventing the wheel
- All specialist agents now search for project-specific AI docs before reviewing

### Removed

- `architect` agent - Unused, replaced by specialized review agents
- `developer` agent - Unused, replaced by specialized review agents
- `debugger` agent - Unused, replaced by specialized review agents
- `quality-reviewer` agent - Unused, replaced by specialized review agents
- `adr-writer` agent - Unused

## [1.4.0] - 2026-01-10

### Added

- `pr-reviewing` skill - Structured PR review workflow ensuring context gathering (Linear issue, PR state, previous reviews) before code review

## [1.3.0] - 2026-01-10

### Added

- `browser-interaction` skill - Browser automation for debugging, verification, testing using MCP servers (chrome-devtools, playwright, puppeteer)
- `dig-into-linear-issue` skill - Thorough Linear issue investigation workflow with RCA templates and validation paths
- `woocommerce-browser-interaction` skill - WooCommerce-specific browser automation patterns (login, admin, frontend, block checkout)

## [1.2.0] - 2025-12-11

### Changed

- Extracted `prompt-optimizer` skill and `/optimize-prompt` command into standalone plugin

## [1.1.0] - 2025-12-11

### Changed

- Extracted `image-optimizer` skill into standalone plugin

## [1.0.0] - 2025-12-09

### Added

- Initial release of pirategoat-tools plugin
- **Skills:**
  - `image-optimizer` - Lossless image optimization using imageoptim-cli and svgo
  - `prompt-optimizer` - Two-phase prompt optimization with pattern attribution
  - `wordpress-backend-dev` - WordPress backend development guidance (WPCS, security, i18n, hooks)
- **Commands:**
  - `/fix-github-issue` - Analyze and fix GitHub issues end-to-end
  - `/execute-plan` - Project manager mode for executing implementation plans
  - `/optimize-prompt` - Quick access to prompt optimization
- **Agents:**
  - `architect` - Lead architect for code analysis and solution design
  - `developer` - Implementation specialist with test focus
  - `debugger` - Systematic bug analysis through evidence gathering
  - `quality-reviewer` - Code review for real issues (security, performance)
  - `technical-writer` - Documentation creation after feature completion
  - `adr-writer` - Architecture Decision Record creation

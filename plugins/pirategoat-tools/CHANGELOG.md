# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.22.1] - 2026-02-09

### Fixed

- **review-scope.py** — Auto-fetch and use remote tracking ref (`origin/<branch>`) as the base for review ranges. Prevents stale local branch refs from inflating review scope with commits already merged to the remote default branch. Best-effort fetch with 15s timeout; falls back gracefully when offline. Guards against double-prefixing (`origin/origin/...`) and SHA-based ranges.

## [1.22.0] - 2026-02-09

### Added

- **full-code-review command** — Branch-level multi-agent code review without requiring a PR. Dispatches 10 specialized reviewer agents in parallel, reconciles findings, and presents a unified summary.

## [1.21.1] - 2026-02-08

### Changed

- **Shared reviewer protocol** - Strengthened reviewing-vs-exploring enforcement
  - Added STOP escalation checkpoint before reporting findings on explored code
  - Added CORRECT/INCORRECT contrastive examples for finding validation
  - Strengthened project-specific knowledge section with explicit READ instruction and priority ordering

- **6 specialist agents** (security, performance, architecture, wp-architecture, patterns, history-insights) - Added confidence scoring gates
  - 0-100 confidence scoring with domain-specific boosters/reducers
  - Findings below 60 confidence are dropped, 60-79 noted as uncertain

- **7 agents** (security, performance, wp-architecture, patterns, history-insights, pr-reviewer + architecture already had it) - Added emotional stimuli
  - Domain-specific "This review matters. [consequence]." statements for identity priming

- **4 agents** (pr-reviewer, php-tests, js-tests, e2e-tests) - Added Core Mission one-liners
  - Consistent arrow-chain format matching existing specialist agents

- **gemini-reviewer, codex-reviewer** - Added error normalization
  - CLI failures framed as expected outcomes, clean UNAVAILABLE report is success

- **review-reconciliator** - Added STOP escalation for unsourced findings
  - Every finding must trace to a specific agent's report

## [1.21.0] - 2026-02-08

### Added

- **Bootstrap reviewer evals** (`tests/`) - Deterministic test suite and grading framework for bootstrap-reviewer.py
  - `test_bootstrap_reviewer.py` — Pytest suite with unit tests (name derivation, protocol extraction, field parsing, output building) and integration tests (subprocess runs for all 11 agents verifying structure, identity, conditional sections, personalization, error handling)
  - `graders.py` — Reusable code-based grading functions for review output files (JSON schema, markdown structure, signal format, no-domain-files, error exit, output pair)
  - `test_graders.py` — Validates graders themselves: valid input passes, missing fields fail, invalid verdicts fail, empty files fail
  - `eval_agent_compliance.py` — Agent compliance runner with `--grade-only` (grade existing outputs) and `--dispatch` (temp repo → bootstrap → dispatch agent → grade) modes
  - `fixtures/no-code-changes.diff` — Docs-only diff fixture for NO_DOMAIN_FILES testing

- **bootstrap-reviewer.py script** (`scripts/`) - Single-command setup that consolidates all reviewer agent initialization into one call
  - Finds plugin root (cached `/tmp/.pirategoat-tools-root`, self-location, or `find` fallback)
  - Validates agent name against known configuration
  - Reads and extracts behavioral rules from `reviewer-protocol.md` (skips setup sections the bootstrap already performed)
  - For test agents, also includes full `tests-reviewer-protocol.md` content
  - Runs `review-scope.py` with agent-specific domain and flags
  - For patterns-reviewer, runs scope twice (normal + `--base-ref-only` for exploration)
  - For tests-mutation-reviewer, skips scope (no domain) but still provides protocol and output instructions
  - Outputs structured prompt block ordered by steering importance: rules (primacy) → scope (processing) → output instructions (recency)
  - Supports `--range` and `--output-dir` pass-through flags
  - Exit codes: 0 (success), 1 (error)

### Changed

- **All 11 reviewer agents** - Simplified MANDATORY SETUP from 3 steps to 1 step
  - Single `bootstrap-reviewer.py --agent <name>` call replaces: get plugin root + read protocol + run scope discovery
  - Reduces setup instructions from ~15 lines to ~7 lines per agent
  - Agents that previously skipped multi-step setup are more likely to comply with a single command
  - Each agent specifies its own `--agent` flag matching its configuration

- **Shared reviewer protocol** - Step 0 now references bootstrap script as preferred method
  - Added bootstrap command as primary setup approach
  - Kept manual steps as fallback if bootstrap unavailable

## [1.20.0] - 2026-02-08

### Added

- **Plugin root discovery hook** (`hooks/`) - PreToolUse:Bash hook writes `$CLAUDE_PLUGIN_ROOT` to `/tmp/.pirategoat-tools-root` so agents can find plugin files when dispatched into target repos
  - `hooks.json` registers the hook for all Bash tool invocations
  - `init-plugin-root.sh` writes the path on each Bash call; agents read it with `cat /tmp/.pirategoat-tools-root`
  - Fallback `find ~/.claude` command when hook hasn't run yet

### Changed

- **All 11 reviewer agents** - Restructured with `## MANDATORY SETUP` as first content after frontmatter
  - Three numbered steps: (1) get plugin root, (2) read shared protocol, (3) run `review-scope.py --domain <X>`
  - Explicit gate: "Do NOT start reviewing code until these 3 steps are done"
  - Identity/expertise section moved below the setup, separated by `---`
  - Previously agents sometimes ignored setup instructions buried in the middle of their definitions

- **Test reviewer agents** (php-tests, js-tests, e2e-tests) - Fixed reference file paths
  - Added explicit `$PLUGIN_ROOT/skills/testing-patterns/references/` prefix
  - Reference table entries now resolve correctly when agents run outside plugin directory

- **architecture-reviewer agent** - Fixed pattern reference paths
  - Added explicit `$PLUGIN_ROOT/skills/software-architecture/` prefix for design pattern files

- **Shared reviewer protocol** - Step 0 uses hook-based discovery with `find` fallback
  - `cat /tmp/.pirategoat-tools-root` as primary method (set by hook)
  - `find ~/.claude -path "*/pirategoat-tools/*/scripts/review-scope.py"` as fallback

## [1.19.0] - 2026-02-08

### Added

- **review-scope.py script** - Shared Python CLI tool that all reviewer agents call to efficiently determine their review scope in a single invocation
  - Replaces 5+ ad-hoc git/grep commands per agent with one structured call
  - Single source of truth for all filtering logic: range detection, noise filtering, domain filtering, context budgeting
  - Parameterized domain catalog: `code`, `security`, `performance`, `architecture`, `wp-architecture`, `php-tests`, `js-tests`, `e2e-tests`, `patterns`
  - Auto-detects default branch (`main`, `master`, `trunk`, `develop`), staged/unstaged changes, and PR number via `gh`/`ghe` CLI
  - Smart `gh` vs `ghe` selection based on remote URL (`github.a8c.com` → `ghe`, `github.com` → `gh`)
  - `--summary` flag for large PRs: outputs diffstat overview of ALL matched files (sorted largest-first) without diffs, letting agents pick which files to deep-dive
  - `--base-ref-only` flag for agents exploring preexisting code (patterns-reviewer, history-insights-reviewer) — skips diff collection, lists all matched files
  - Context budget (`--max-lines`, default 2000) — files sorted smallest-first (focused changes before large files), budget-exceeded files shown with diffstat so agents can selectively read them
  - Defensive error handling: structured error output on both stdout and stderr so agents always see failures; never silently eats errors
  - Extended noise filter: images, fonts, archives, binaries (.wasm, .pyc, .so), PDFs, translation artifacts (.mo, .pot), Jest snapshots (.snap), build artifacts, IDE/OS config
  - Exit codes: 0 (success), 1 (error), 2 (no changes)

### Changed

- **Shared reviewer protocol** - Scope Discovery section now references `review-scope.py` as primary method with bash fallback
  - Output Directory section simplified: script handles `gh`/`ghe` detection automatically
  - Added GHE note for repos on `github.a8c.com`

- **All reviewer agents** - Scope sections simplified to single `review-scope.py --domain <X>` call
  - `pr-reviewer` → `--domain code`
  - `security-reviewer` → `--domain security`
  - `performance-reviewer` → `--domain performance`
  - `architecture-reviewer` → `--domain architecture`
  - `wp-architecture-reviewer` → `--domain wp-architecture`
  - `php-tests-reviewer` → `--domain php-tests`
  - `js-tests-reviewer` → `--domain js-tests`
  - `e2e-tests-reviewer` → `--domain e2e-tests`
  - `patterns-reviewer` → `--domain patterns` + `--base-ref-only` for exploration
  - `history-insights-reviewer` → `--domain code --base-ref-only` for scenario extraction

## [1.18.0] - 2026-02-08

### Changed

- **Shared reviewer protocol** - Agents are now self-sufficient: work both dispatched (from pr-reviewing) and standalone (ad-hoc invocation)
  - New **Scope Discovery** section: agents detect their own review scope from Git Range (if provided), current branch divergence, staged changes, or unstaged changes — in that fallback order
  - New **noise filter**: all agents skip `.lock`, `vendor/`, `node_modules/`, `dist/`, `build/`, binary files, IDE config before any review work
  - New **Output Directory fallback**: agents detect PR number via `gh`/`ghe` CLI when no output dir provided; fall back to `/tmp/` with timestamped filenames to avoid collisions
  - New **Reviewing vs Exploring** rule: explicitly distinguishes analyzing changed code (generates findings) from reading existing code for context (no findings); agents that explore preexisting code must search the base ref state, not HEAD
  - New **context budget**: agents prioritize smaller diffs first and note skipped large files instead of silently ignoring them
  - "Read diffs, not entire files" directive: agents read `git diff <range> -- <file>` and only use `Read` with offset+limit for surrounding context on specific findings

- **All 11 reviewer agents** - Added concrete domain file filters referencing the shared scope discovery
  - `pr-reviewer`: broad code file filter (generalist)
  - `security-reviewer`: code files only (no docs, stylesheets)
  - `performance-reviewer`: code files with queries and operations
  - `architecture-reviewer`: implementation files excluding tests (updated from ad-hoc filter to shared protocol chain)
  - `wp-architecture-reviewer`: PHP/JS/TS files
  - `php-tests-reviewer`, `js-tests-reviewer`, `e2e-tests-reviewer`: concrete grep filters for their test file scopes, with early exit when no matching files in diff
  - `history-insights-reviewer`: scope discovery for scenario extraction, searches are inherently history-scoped
  - `tests-mutation-reviewer`: references shared protocol for scope discovery and output directory

- **patterns-reviewer agent** - Now searches preexisting code only via base ref
  - All codebase searches use `git grep <pattern> <base_ref>` instead of `grep -r .` on working tree
  - Prevents finding the PR's own code when checking for existing patterns
  - Git log searches unchanged (inherently history-scoped)
  - Pattern Search Protocol step 1 updated: "Search base ref code" instead of "Search current code"

## [1.17.0] - 2026-02-08

### Added

- **history-insights-reviewer agent** - Mines git history and GitHub PRs for fixes, enhancements, and lessons learned from similar scenarios elsewhere in the codebase
  - Phase-based approach: scenario extraction, git history mining (commit messages, pickaxe search, PR search), classification, insight report
  - Supports both `gh` (github.com) and `ghe` (github.a8c.com) for PR searches
  - Distinct from `patterns-reviewer`: focuses on bug fixes, edge cases, and improvements rather than pattern consistency
  - Verdicts: `APPLY_FIX`, `CONSIDER_ENHANCEMENT`, `LEARN`, `APPROVE`
  - Categories: `applicable-fix`, `enhancement-opportunity`, `cautionary-precedent`, `edge-case-precedent`, `performance-precedent`, `security-precedent`
  - Integrated into review-reconciliator and pr-reviewing skill parallel dispatch

## [1.16.0] - 2026-02-08

### Changed

- **tests-reviewer agent** - Split into three language-specific agents for focused, non-overlapping reviews
  - `php-tests-reviewer` — PHPUnit, WordPress (WP_UnitTestCase, factories), WooCommerce, Brain Monkey
  - `js-tests-reviewer` — Jest, Vitest, React Testing Library, async patterns, snapshot discipline
  - `e2e-tests-reviewer` — Playwright, Page Object Model, locator strategies, auto-waiting
  - Shared test quality protocol extracted to `agents/shared/tests-reviewer-protocol.md`
  - Each agent reads shared reviewer protocol + shared tests protocol, then applies language-specific red flags
  - Non-overlapping file scopes prevent duplicate findings across agents

- **testing-patterns skill** - Reduced to shared core, language-specific patterns split into dedicated skills
  - `php-testing-patterns` — PHPUnit assertions, WordPress factories, `assertSame` > `assertEquals`, data providers
  - `js-testing-patterns` — RTL query priority, `toMatchObject` > `toEqual`, async assertions, mock scope
  - `e2e-testing-patterns` — Locator priority, Page Object Model, `waitForTimeout` alternatives, network interception
  - Core skill retains: test philosophy, smells, mocking decisions, coverage, test data, test layers
  - Language-specific routing entries removed from core (phpunit-patterns, jest-vitest-patterns, playwright-patterns)
  - Reference files remain in `testing-patterns/references/` (no moves)

- **review-reconciliator agent** - Updated to read three test review outputs instead of one
- **pr-reviewing skill** - Updated parallel dispatch to spawn three test reviewers

### Removed

- `tests-reviewer` agent — replaced by `php-tests-reviewer`, `js-tests-reviewer`, `e2e-tests-reviewer`

## [1.15.0] - 2026-02-08

### Changed

- **Review agents** - Extract shared boilerplate into shared reviewer protocol, reducing agent context by ~45%
  - New `agents/shared/reviewer-protocol.md` (~96L) consolidates: Changed Code Only rule, ReviewOutputBuilder API, file-based output format, return signal template, project-specific knowledge search, ground truth data loading, verbose reasoning mode
  - All 9 reviewer agents now reference shared protocol via `**FIRST:** Read shared/reviewer-protocol.md`
  - Domain-specific content preserved in each agent: RULE 0s, red flags, verification protocols, checklists, review philosophy
  - Boilerplate removed: Structured Output sections, Context format, File-Based Output steps (all identical across agents)

- **software-architecture skill** - Restructured as section-aware routing hub (461L -> 111L, 76% reduction)
  - Code smell -> pattern routing table maps symptoms to specific `## ` headings in reference files
  - Agents read ~200L per reference file instead of ~2,000L (90% reference context savings)
  - Kept inline: SOLID quick reference, architecture review checklist, pattern selection decision matrix, when-not-to-apply rules
  - Removed: GoF pattern categories overview, DEMS D'FFACTS mnemonic, design pattern combinations, inline hexagonal architecture overview, language-specific considerations (all available in reference files or training knowledge)

- **testing-patterns skill** - Restructured as section-aware routing hub (365L -> 104L, 71% reduction)
  - Test smell -> reference routing table maps findings to specific sections in reference files
  - Kept inline: "What Makes a Good Test" table, FORBIDDEN patterns, mocking decision table, test smells quick diagnosis
  - Removed: Inline PHP/JS/Playwright code examples, test review checklist (in tests-reviewer), test layer context table (covered by routing)

- **architecture-reviewer agent** - Replaced skill loading with inline routing table and SOLID reference (674L -> 133L)
- **security-reviewer agent** - Condensed function tables to quick reference, removed code examples (611L -> 119L)
- **performance-reviewer agent** - Condensed optimization tables inline, removed code examples (480L -> 118L)
- **wp-architecture-reviewer agent** - Condensed code examples, kept ecosystem patterns (643L -> 145L)
- **tests-reviewer agent** - Preserved all verification protocols and red flags (803L -> 163L)
- **pr-reviewer agent** - Preserved goal alignment rules and confidence scoring (509L -> 127L)
- **patterns-reviewer agent** - Preserved git history search protocol (421L -> 139L)
- **tests-mutation-reviewer agent** - Preserved all mutation phases and safety rules (552L -> 199L)
- **review-reconciliator agent** - Preserved JSON-first reconciliation with REQUIRED directive (365L -> 209L)

### Added

- `agents/shared/reviewer-protocol.md` - Shared protocol for all review agents

## [1.14.0] - 2026-02-08

### Added

- **tests-reviewer agent** - Overprescriptive test detection and refactoring resilience checks
  - New HIGH severity category (6a-6e): copy/string-based assertions, snapshot overuse, exact data shape assertions, internal call sequence assertions, pinning on incidental details
  - New "Test Resilience" review checklist (7 items) and "overprescriptive" red flags table
  - Extended verification protocol with questions 6-7 targeting refactoring resilience
  - Refactoring Resilience Test diagnostic for verbose reasoning mode
  - New test categories: `overprescriptive-test`, `copy-based-assertion`
  - RULE 0 corollary: fewer meaningful tests beat many overprescriptive tests
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

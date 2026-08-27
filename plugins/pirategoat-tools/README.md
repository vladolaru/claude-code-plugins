# pirategoat-tools

My main Claude Code and Codex plugin - the Swiss army knife I reach for on every project. Started as a personal grab bag of experimental features and grew into a proper toolkit for code review, testing, architecture, and WordPress development.

Everything here is opinionated, actively used, and evolving.

## What's Inside

### 34 Agents

#### 28 Domain Review Agents

These run in parallel by default — total review time equals the slowest agent, not the sum of all agents.

| Agent | Focus | Model |
|-------|-------|-------|
| **code-reviewer** | Generalist — validates changes against stated goals, catches cross-cutting issues | opus |
| **security-reviewer** | WordPress security — SQL injection, XSS, CSRF, capabilities, sanitization | sonnet |
| **architecture-reviewer** | Design patterns, SOLID principles, coupling/cohesion (language-agnostic) | sonnet |
| **wp-architecture-reviewer** | WordPress-specific — hooks, extensibility, WPCS, backwards compatibility | sonnet |
| **woo-regression-reviewer** | WooCommerce regression invariants — Action Scheduler traps, meta/sync-on-read loops, template overrides, broken-until-JS defaults, heuristic proxy predicates vs. config variance, contract breaks with out-of-tree blast radius (WC core/extensions only) | opus |
| **ecosystem-integration-reviewer** | Integration correctness against upstream runtime hosts — filter/action callback signatures, class override correctness, REST route schemas | sonnet |
| **performance-reviewer** | N+1 queries, caching, autoloaded options, WP_Query optimization | sonnet |
| **php-tests-reviewer** | PHPUnit test quality, WordPress factories, WooCommerce patterns | sonnet |
| **js-tests-reviewer** | Jest/Vitest quality, React Testing Library queries, async patterns | sonnet |
| **e2e-tests-reviewer** | Playwright quality — locators, Page Object Model, auto-waiting | sonnet |
| **go-tests-reviewer** | Go testing idioms, table-driven tests, httptest, benchmarks | haiku |
| **rust-tests-reviewer** | Rust test framework, assert macros, async tests, mockall, proptest, insta | haiku |
| **python-tests-reviewer** | pytest fixtures, mock/patch, parametrize, pytest-asyncio, hypothesis, factory_boy | haiku |
| **patterns-reviewer** | Codebase archaeology — finds existing patterns, prevents reinventing the wheel | sonnet |
| **dead-code-reviewer** | Unused functions, unreachable paths, orphaned imports | sonnet |
| **history-insights-reviewer** | Mines git history for relevant prior fixes and lessons learned | sonnet |
| **tests-mutation-reviewer** | Fault injection to verify tests catch real bugs (runs solo) | sonnet |
| **a11y-reviewer** | ARIA correctness, keyboard access, focus management, WCAG 2.2 AA — any UI-emitting language (JSX, server-rendered PHP/HTML, templates) | opus |
| **reliability-reviewer** | Logging, error handling, rollback safety, feature flags, failure-mode resilience | sonnet |
| **api-contract-reviewer** | API contract stability — backwards-incompatible changes, response shape drift, missing deprecation | sonnet |
| **data-flow-privacy-reviewer** | PII in logs, data leakage in API responses, GDPR erasure gaps, payment data handling | sonnet |
| **concurrency-reviewer** | Race conditions, TOCTOU, missing transactions, cache stampede, idempotency | sonnet |
| **code-clarity-reviewer** | Naming accuracy, documentation correctness, name-behavior mismatches, stale docblocks | sonnet |
| **docs-drift-reviewer** | Documentation drift — stale README, CLAUDE.md, AGENTS.md, API docs after code changes | sonnet |
| **toolchain-reviewer** | Package manager configs, build tools, linting configs, version constraints, CI pipelines, supply chain settings | sonnet |
| **reference-integrity-reviewer** | Reference resolution — verifies plugin slugs, asset paths, URLs, and config references point to real targets | sonnet |
| **simplification-reviewer** | Unnecessary complexity — over-abstraction, premature generalization, unnecessary indirection | sonnet |
| **devils-advocate-reviewer** | Fundamental approach questioning — reframes problems to find simpler paths (50+ lines, evidence-gated) | opus |

#### 2 Pipeline Agents

These are not domain reviewers — they synthesize and validate review output from the domain agents.

| Agent | Role | Model |
|-------|------|-------|
| **review-reconciliator** | Aggregates findings from all agents into a single prioritized summary | sonnet |
| **decision-reviewer** | Stress-tests review conclusions via structured criticism | opus |

#### 2 Cross-Validators

External LLM cross-validation — shell out to other CLI tools for independent perspective.

| Agent | Role | Model |
|-------|------|-------|
| **gemini-reviewer** | Cross-validates via Google Gemini CLI | haiku |
| **codex-reviewer** | Cross-validates via OpenAI Codex CLI | haiku |

#### 2 Utility Agents

| Agent | Role | Model |
|-------|------|-------|
| **technical-writer** | Creates documentation after feature completion | haiku |
| **repo-reviewer-adapter** | Runs a reviewer prompt contributed by the repo under review (declared in `.pirategoat/config.json`) and normalizes its findings into the standard format | inherit |

#### Model Tiers

Not all work requires the same level of reasoning. Agents are assigned to model tiers based on what their task demands:

- **opus** (5 agents) — Deep judgment work requiring nuanced reasoning. The code-reviewer must understand change intent and exercise blocker-vs-preference decisions. The a11y-reviewer needs contextual reasoning about accessibility impact. The decision-reviewer needs full reasoning depth for adversarial analysis of review conclusions. The devils-advocate-reviewer questions fundamental approach choices on substantial PRs. The woo-regression-reviewer needs deep domain judgment to weigh heuristic proxy predicates against genuine store-configuration variance across WooCommerce's regression-prone surfaces.
- **sonnet** (22 agents) — Structured analysis against well-defined checklists. The review-reconciliator performs judgment-heavy synthesis — conflict resolution, deduplication, and 10:1 compression across all agent outputs. Architecture reviewers apply SOLID principles and WordPress ecosystem patterns. Security tracing follows a source-to-sink framework. Performance detection matches known antipatterns (N+1, unbounded queries). The reliability reviewer checks error handling, rollback safety, and observability against concrete checklists. The API contract reviewer detects backwards-incompatible changes against public interfaces. The data flow/privacy reviewer traces PII through code paths. The concurrency reviewer identifies race conditions and missing transactions. The code-clarity reviewer catches naming-behavior mismatches and stale inline documentation with behavioral proof. The docs-drift reviewer detects when code changes cause external documentation (README, CLAUDE.md, guides) to become stale. The toolchain reviewer verifies package manager configs, build tool settings, and CI pipelines against actual tool versions via changelog research. Test reviewers check against catalogued smells. The patterns and history-insights reviewers search for codebase precedents. The mutation reviewer follows a rigid 5-phase protocol. The dead-code reviewer traces dependency graphs. All of these benefit from competence but don't need the deep ambiguity-resolution that the most capable models provide.
- **haiku** (6 agents) — Orchestration or highly mechanical work. The gemini-reviewer and codex-reviewer just build prompts, shell out to external CLIs, and parse responses. The technical-writer fills token-constrained templates. The go-tests-reviewer, rust-tests-reviewer, and python-tests-reviewer match against highly standardized testing idioms — nearly every finding maps to a known pattern.

### 22 Skills

| Skill | What it brings |
|-------|---------------|
| **testing-patterns** | Test quality patterns with a 190KB reference library — philosophy, smells, TDD workflow |
| **php-testing-patterns** | PHPUnit assertions, WordPress test utilities, WooCommerce patterns, Brain Monkey |
| **js-testing-patterns** | Jest/Vitest assertions, React Testing Library queries, async patterns, snapshots |
| **e2e-testing-patterns** | Playwright locators, Page Object Model, auto-waiting, network interception |
| **go-testing-patterns** | Standard testing package, table-driven tests, httptest, benchmarks, fuzz testing |
| **rust-testing-patterns** | Built-in test framework, assert macros, async tests, mockall, proptest, rstest, insta, criterion |
| **python-testing-patterns** | pytest fixtures, parametrize, mock/patch, pytest-asyncio, hypothesis, freezegun, factory_boy |
| **software-architecture** | GoF patterns, SOLID, hexagonal architecture with an 87KB pattern library |
| **wordpress-backend-dev** | WPCS coding standards, security patterns, i18n, hooks API, REST API |
| **browser-interaction** | Browser automation via MCP servers (chrome-devtools, playwright) |
| **woocommerce-browser-interaction** | WooCommerce-specific browser workflows — login, admin, checkout |
| **dig-into-linear-issue** | Linear issue investigation with RCA templates |
| **current-datetime** | Verify current date/time before writing timestamps, plus time zones, date arithmetic, scheduling |
| **decision-critic** | Structured decision analysis for technical trade-offs |
| **creating-md-slides** | Markdown presentations via Marp (PDF, PPTX, HTML) |
| **marp-slide-quality** | SlideGauge integration for presentation analysis |
| **accessible-frontend-dev** | ARIA correctness, keyboard operability, focus management, WCAG 2.2 AA |
| **using-figma** | Figma-to-code workflow — survey, specification, component tree, implementation, validation |
| **figma-copy-sync** | Synchronize text copy between Figma designs and implemented code |
| **analyzing-cc-sessions** | Parse CC session JSONL transcripts, analyze subagent behavior, extract metrics |
| **analyzing-codex-sessions** | Parse Codex CLI rollout JSONL logs, investigate Codex subagent behavior, extract thread metrics |
| **create-github-pr** | Structured PR creation workflow with pre-flight checks, context gathering, and approval gate |

### 7 Commands

| Command | Purpose |
|---------|---------|
| `/full-code-review` | Run all review agents in parallel on current branch changes |
| `/code-review` | Incremental review of new commits since the last review |
| `/pr-review` | End-to-end PR review pipeline (context + agents + validation) |
| `/iterative-review` | Multi-round independent Codex review with pushback tracking and convergence detection |
| `/pr-update` | Update PR description with accurate summary of current changes |
| `/copy-as [content] [slack\|p2]` | Copy content to clipboard — markdown, Slack mrkdwn, or P2 HTML |
| `/switch-to <branch\|PR_URL>` | Switch to a branch or PR — handles dirty state, remote sync, fork remotes, and post-switch context |

Codex installs generated skill adapters for these commands. Invoke them with
the explicit plugin skill syntax, such as `$pirategoat-tools:pr-review 42`.
The adapters are generated from the command files, so the workflow has one
canonical source.

### Pipeline Analytics

`scripts/analysis/review_run_metrics.py` is the supported interface for measuring review pipeline runs and recent cohorts. It treats pipeline telemetry and its durable manifest as authoritative, then optionally enriches an exact run from its Claude session and correlated subagent transcripts.

```bash
# Recent review-run cohort
python3 scripts/analysis/review_run_metrics.py --last 30

# Stable JSON for longitudinal analysis
python3 scripts/analysis/review_run_metrics.py --last 30 --format json --output "$TMPDIR/review-runs.json"

# One pipeline-native run without transcript correlation
python3 scripts/analysis/review_run_metrics.py --run-id <run-id> --no-transcripts
```

Important: the stable JSON report is local operational output, not an anonymized or share-safe export. It intentionally retains `repo_path`, `output_dir`, `session_id`, Git range/SHA identifiers, and free-form main-orchestrator adjustment reasons because they are measurement evidence. Transcript privacy reduction excludes raw prompt bodies, source and finding prose, commands, and tool-result bodies; it does not make the report path-free or identifier-free. Sanitize or redact generated JSON before sharing it outside the local trusted context.

The stable JSON report uses schema v2. It keeps `complete`, `partial`, `missing`, and `disabled` availability distinct from a measured zero. Generated-scope coverage describes what the pipeline assigned; it does not prove what a model read. Transcript-derived observed reads use a strict v2 payload and are explicitly non-exhaustive: reviewer reads form the `all`/`in_scope`/`out_of_scope` partition, while exact `review-reconciliator`, `decision-reviewer`, and `critic` reads are reported separately as non-scope-comparable synthesis activity. Those two actor families have independent completeness, availability, and cohort denominators; the combined `observed_reads` availability is only the conservative conjunction. Every retained read is a canonical repository-relative path. Legacy or mismatched payload versions and any absolute, traversal, non-canonical, backslash-separated, or control-character path fail closed as unavailable rather than being zero-filled.

The `synthesis_agents` family measures the two agents the reviewer lifecycle structurally cannot see — the review-reconciliator (step 8) and the decision critic (step 10), neither of which appears in a dispatch plan or emits agent lifecycle events. Each dispatched one carries a duration measured from its completion artifact's mtime (`review-findings.json` and `decision-critic-verdict.json`, the artifacts each step's handoff gate makes mandatory). Quick mode commits the pipeline's own `SKIPPED` verdict without a dispatch marker, so it gets no lifecycle row rather than a zero; a run predating the family reports `missing`; and a dispatched critic whose usable verdict never appears reports `stalled` with no duration, reads `unavailable`, and degrades the run. Historical `SKIPPED` rows remain readable but are counted separately as `skipped_runs` instead of entering critique-duration statistics. The policy is report, never kill: both agents run in the orchestrator's foreground, where nothing downstream can interrupt them.

Lifecycle `agents.incomplete` is a sorted multiset: an agent name repeats once for every start execution not matched by a completion. Run and cohort summaries report `incomplete_count` as the unmatched execution total, `incomplete_identities` as unique sorted names, and `incomplete_by_agent` as deterministic per-agent execution counts. Complete manifests validate this multiset exactly and remain authoritative. Running manifests remain partial observations; ingestion can retain newer append-only agent events from the same run only after proving the sidecar lifecycle is an exact causal prefix, and reduces that suffix without copying raw prose or scope paths. Invalid sibling logs make lifecycle unavailable without discarding other sidecar metric families.

There are no human overrides in this flow. Deterministic planning runs first; the main orchestrator may then add or skip agents and supplies the adjustment reasons. Dispatch aggregates retain `adjustment_rate` as the share of changed agents across the full compared-agent union, including unchanged skips, and expose `planner_removal_rate` separately as removed agents divided by planner-dispatched candidates in comparable runs. When two valid plans contain different agent identity sets, adjustment comparison is unavailable, but sorted identity-to-status projections let ingestion rederive and validate each plan's dispatch count before those partial totals enter a cohort. Malformed, contradictory, or out-of-mode projections fail closed for the dispatch family without exposing plan prose. Wall durations above one year are treated as implausible missing data before cohort statistics are calculated.

`scripts/analysis/session_metrics.py` remains the lower-level, general-purpose transcript metrics tool for ad hoc agent-performance and triage investigations. See each script's `--help` for all options. `scripts/analysis/codex_session_analyzer.py` and `scripts/analysis/codex_session_metrics.py` are the Codex CLI equivalents, covering `~/.codex/sessions` rollouts. See the `analyzing-codex-sessions` skill for the format reference.

## Installation

### Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
```

### Codex

```bash
codex plugin marketplace add vladolaru/claude-code-plugins
codex plugin add pirategoat-tools@vladolaru-claude-code-plugins
```

The Codex review path uses native parallel subagents. Each subagent receives
the same canonical reviewer file used by Claude Code. Claude-specific model
labels are not translated because the hosts expose different model catalogs.

## How Reviews Work

1. Check for project-specific context (AGENTS.md, CLAUDE.md, skills)
2. Load skill knowledge (testing-patterns, software-architecture)
3. Analyze code changes
4. Report findings with confidence scores
5. Output both JSON (automation) and Markdown (humans)

All output is dual-format — `.json` for automation, `.md` for reading.

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Changelog](./CHANGELOG.md) | Detailed version history |
| [Guides](./docs/guides/) | User guides and tutorials |

## Structure

```
pirategoat-tools/
├── .codex-plugin/
│   └── plugin.json   # Generated Codex manifest
├── agents/           # 34 agent definitions (28 reviewers, 2 pipeline, 2 cross-validators, 2 utility)
├── codex-skills/     # 7 generated Codex command adapters
├── commands/         # 7 slash commands
├── skills/           # 22 shared skills
│   ├── testing-patterns/references/      # 190KB test quality library
│   └── software-architecture/patterns/   # 87KB design pattern library
├── scripts/          # Helper scripts organized by domain
│   ├── review/             # Review pipeline, dispatch, context, telemetry
│   │   ├── pipeline.py           # Executable facade: routing, state, output, telemetry, CLI
│   │   ├── pipeline_contract.py  # Shared host, step, timeout, path, and Git vocabulary
│   │   ├── briefings.py          # Pure curated guidance and briefing formatters
│   │   ├── orchestration.py      # Side-effecting per-step subprocess and artifact work
│   │   ├── dispatch_status.py  # Canonical dispatch vocabulary + plan validation
│   │   └── agent/          # Agent bootstrap, scope filtering, output builder
│   ├── hosts/              # Upstream host discovery (host_context CLI, chain, resolvers, ecosystem_cache)
│   │   └── cache/          # Internal ecosystem-cache manager (WordPress + WooCommerce)
│   ├── linear/             # Linear issue pipeline, events
│   ├── figma/              # Figma spec extraction, node parsing
│   ├── analysis/           # Session analysis, metrics extraction
│   └── iterative_review/   # Multi-round Codex review loop sub-module
├── hooks/            # Git hook integrations
├── schemas/          # JSON schemas for review output
├── docs/             # Documentation and guides
├── tests/            # Deterministic eval suite (mirrors scripts/ structure)
│   ├── review/              # Review pipeline + agent tests
│   │   └── agent/           # Bootstrap, scope, output tests
│   ├── linear/              # Linear issue pipeline tests
│   ├── iterative_review/    # Iterative review loop tests
│   ├── analysis/            # Session analysis tests
│   ├── commands/            # Command structure tests
│   ├── grading/             # Graders + compliance evals
│   └── helpers/             # Shared test utilities
└── CHANGELOG.md
```

## License

MIT — see [LICENSE](../../LICENSE).

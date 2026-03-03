# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.43.6] - 2026-03-03

### Fixed

- **ingest-preprocess.py:** Handle `reconciled-structured.json` in clusters-only format (old `reconcile-reviews.py` without the flat `issues` key added in step 8.5). When the structured file has `clusters` but no `issues`, the preprocessor now extracts canonicals from the clusters instead of silently returning zero findings. Added regression test `test_clusters_format_without_issues_key`.

## [1.43.5] - 2026-03-02

### Added

- **Figma helper scripts:** `figma-parse-nodes.py` (Phase 0 metadata parsing) and `figma-extract-specs.py` (Phase 1 design context extraction) referenced by the using-figma skill
- **Design spec template:** `references/design-spec-template.md` for the using-figma skill

### Removed

- **execute-plan command:** Unused, removed along with stale `quality-reviewer` reference
- **fix-github-issue command:** Unused
- **CURRENT-STATUS.md:** Stale since v1.10.0, actively misleading at v1.43.4

## [1.43.4] - 2026-03-02

### Added

- **analyzing-cc-sessions skill:** Ship skill for parsing CC session JSONL transcripts, analyzing subagent behavior, and extracting metrics — was registered in marketplace.json but never committed

### Fixed

- **Skills (analyzing-cc-sessions, decision-critic, using-figma):** Use skill base directory derivation for script path resolution instead of bare relative paths — scripts now resolve correctly when installed from marketplace cache
- **ingest-code-review command:** Use `${CLAUDE_PLUGIN_ROOT}` for all script paths instead of bare `scripts/` references

### Changed

- **Skills (analyzing-cc-sessions, woocommerce-browser-interaction, browser-interaction):** Remove local setup references and project-specific paths — keep examples generic for any user

## [1.43.3] - 2026-03-02

### Fixed

- All reviewer agents + shared protocol: validate plugin root cache path exists before use, and pick latest (not oldest) cached version in find fallback — prevents agents from running scripts from old cached plugin versions after upgrades

## [1.43.2] - 2026-03-02

### Changed

- **patterns-reviewer agent:** Add parallel tool call guidance — instructs the agent to issue independent `git grep` and `git show` calls simultaneously instead of sequentially. Addresses the #1 inefficiency (43.7% of all tool calls are sequential git grep, with zero parallelism across 302 observed turns). Expected ~40% wall-clock reduction on the search phase.

## [1.43.1] - 2026-03-02

### Added

- **test_extract_session_metrics.py:** 16 unit tests for `identify_agent_type()` — covers bootstrap detection (Strategy 1), reconciliator fingerprinting (Strategy 1.5), hardened keyword inference (Strategy 2), and edge cases (empty files, list content format, mixed signals)

### Fixed

- **extract-session-metrics.py:** Reconciliator agent sessions no longer misidentified as wp-architecture-reviewer. Added fingerprint detection (Strategy 1.5) and agent signal line stripping in keyword inference (Strategy 2) to prevent false matches from orchestrator context text like "wp-architecture-reviewer: STATUS=COMPLETED"

### Changed

- **history-insights-reviewer:** Cap diff output at 500 lines (`--max-lines 500`) and file history at 5 commits per file (down from 15) to reduce Sonnet context-processing time after the Opus→Sonnet demotion caused a 20% speed regression

## [1.43.0] - 2026-03-01

### Added

- **figma-copy-sync skill:** Self-contained skill for synchronizing text copy between Figma designs and implemented code. 4-phase workflow: Figma text extraction → surface matching (browser snapshots via browser-interaction skill) → copy comparison with i18n detection → approval-gated application. Handles multiple component states, auto-detects translation patterns (WordPress i18n, react-intl, i18next), and produces structured sync reports.

### Changed

- **figma-copy-sync skill:** Optimize with prompt engineering patterns — identity establishment, tiered Iron Rules (Safety-Critical RULE 0-2 vs Operational RULE 3-5), structured HITL approval gates with impact summaries, error normalization for expected uncertainty, compact workflow table replacing unrenderable dot graph, and affirmative directive framing

## [1.42.2] - 2026-03-01

### Added

- **test_review_output.py:** 40 unit tests for ReviewOutputBuilder — initialization, issue validation, verdict calculation, serialization (dict, JSON, markdown), and file output
- **test_review_api_contract.py:** 12 cross-component contract tests verifying producer→reconcile→ingest pipeline — catches interface mismatches between layers

### Fixed

- **reconcile-reviews.py:** Add `issues` key to reconcile output that flattens `clusters[].canonical` into a flat list. Previously `ingest-preprocess.py` read `reconciled.get("issues", [])` but reconcile only wrote `clusters`, silently dropping all findings. The `clusters` key is preserved for backward compatibility.

## [1.42.1] - 2026-03-01

### Changed

- **Bootstrap integration tests:** Run against temporary mock git repos (from `.diff` fixtures) instead of the real repository, eliminating state-dependent test results
- **conftest.py:** New shared test helper with `setup_temp_git_repo()` extracted from `test_domain_routing.py` — creates isolated git repos from diff fixtures for any test module
- **TESTING.md:** Documented mock repo pattern as design principle #8

## [1.42.0] - 2026-03-01

### Added

- **agent-registry.json:** Canonical JSON registry for all 15 review agents — single source of truth replacing hardcoded AGENT_CONFIG in bootstrap-reviewer.py. Fields: domain, secondary_domains, protocols, scope_flags, dispatch_class, triage_criteria, focus, model_tier
- **plan-review-dispatch.py:** Deterministic dispatch planner that reads agent registry and changed files to decide which agents to dispatch. Replaces duplicated triage/dispatch logic in command files with `--mode full|incremental|pr`
- **reconcile-reviews.py:** Deterministic reconciliation engine with Jaccard-based dedup clustering, severity resolution, and structured output. Pre-processes findings before the LLM reconciliator agent
- **ingest-preprocess.py:** Deterministic scope checker and pre-classifier for ingest pipeline. Reduces LLM ingest steps from 6 to 3 by handling file/hunk scope checks and stable ID assignment mechanically
- **reliability-reviewer agent:** New conditional agent for operational resilience review — logging, error handling, rollback safety, feature flags, circuit breakers, and failure-mode handling (sonnet tier)
- **config-ops domain:** New scope domain covering CI/CD configs, Docker, Terraform, Helm, Makefiles, and infrastructure files. Security-reviewer and architecture-reviewer gain secondary domain coverage with dedicated checklists
- **reliability domain:** New scope domain for production code operational resilience review
- **Quality metrics extraction:** `--quality-metrics` mode in analyze-reviewer-sessions.py for finding counts, survival rates, and cross-agent overlap detection
- **Test adequacy advisory:** Informational test-gap detection in reconcile-reviews.py — warns when production code changes without corresponding test modifications
- **180+ new tests:** test_agent_registry (27), test_dispatch_planner (41), test_reconcile_reviews (55), test_ingest_preprocess (30), test_quality_metrics (27), domain routing extensions

### Changed

- **bootstrap-reviewer.py:** Loads agent config from agent-registry.json instead of hardcoded dict; secondary_domains support for multi-domain scope discovery
- **review-reconciliator.md:** Simplified from mechanical dedup+narrative to narrative-only — reads pre-processed reconciled-structured.json, focuses on synthesis and executive summary
- **ingest-code-review.py:** Supports 3-step preprocessed mode (--total-steps 3) alongside legacy 6-step mode for backwards compatibility
- **full-code-review.md:** Dispatch via plan-review-dispatch.py; reconciliation split into deterministic preprocessing + LLM narrative
- **code-review.md:** Same dispatch and reconciliation refactoring as full-code-review.md
- **pr-review.md:** Updated agent count to /14; references dispatch planner directly

### Fixed

- **pr-review.md:** Corrected hardcoded agent count from /12 to /14
- **README.md:** Fixed 5 stale model tier entries; corrected tier counts (inherit 3, sonnet 12, haiku 4)

## [1.41.3] - 2026-03-01

### Changed

- **analyzing-cc-sessions:** Apply prompt engineering optimizations for behavioral clarity:
  - **"Before You Start" goal table** (Pre-Work Context Analysis) — maps analysis goals to concrete starting points, eliminating aimless exploration
  - **"Selecting Task-Relevant Agents"** (Affirmative Directives) — reframes "skip compaction agents" into "process only task agents" with reusable `is_task_agent()` helper
  - **Scripts table by use case** (Category-Based Generalization) — reorganizes from flat "Script | Purpose" to "When you need to... | Use" with fallback row for custom analysis
  - **Parsing error preamble** (Error Normalization) — sets defensive expectations for malformed data upfront, preventing parser crashes
  - **Efficiency analysis guidance** (Hint-Based Guidance) — directs focus to phase transition boundaries where waste clusters
- **review-reconciliator:** Change model from `sonnet` to `inherit` — the reconciliator performs judgment-heavy synthesis (conflict resolution, deduplication, 10:1 compression) so it should use the parent session's model rather than being pinned to Sonnet

## [1.41.2] - 2026-03-01

### Changed

- **using-figma:** Apply prompt engineering optimizations for high-impact behavioral improvements:
  - **Red Flags → Pre-Action Checkpoints** (STOP Escalation + Affirmative Directives) — converts passive "if you catch yourself" observation into active pre-action verification table with trigger/test/alternative columns
  - **Iron Rules → Category-Based Generalization** — groups 9 rules into 3 principle categories (data acquisition first, structural understanding first, tool usage discipline) enabling analogical reasoning for unlisted scenarios
  - **Asset Handling → Affirmative Directives** — reframes 3 prohibitions ("Do NOT") into 3 affirmative directives specifying correct behavior directly
  - **New "Handling Figma MCP Failures" section** (Error Normalization) — adds recovery table for truncated responses, empty results, connection errors, and unexpected formats to prevent apology spirals

## [1.41.1] - 2026-03-01

### Changed

- **patterns-reviewer:** Add tool discipline instruction — Bash only for git commands, use Read/Grep/Glob for everything else (addresses inefficiency #5 from deep analysis: 23 `cat`/`head`/`find` calls via Bash in worst dispatch)
- **patterns-reviewer:** Add search scoping guidance — always include extension filters and directory paths in `git grep`, never search unscoped common words (addresses inefficiency #6 remainder: broad searches like `git grep "error"` wasting 3-4 refinement calls)

## [1.41.0] - 2026-03-01

### Added

- **New `analyzing-cc-sessions` skill** — Reference guide for navigating and analyzing Claude Code raw session logs (JSONL transcripts). Codifies structural knowledge from 3 deep analysis sessions (figma-workflow, dead-code-reviewer efficiency, patterns-reviewer deep analysis). Covers:
  - Session data locations and project directory resolution
  - Main session JSONL structure (5 entry types, content block formats, tool_use/tool_result pairing)
  - Subagent JSONL structure (simpler format, dispatch prompt identification, agent type inference)
  - Tool results persistence (>30KB threshold, separate files)
  - Correlating main session dispatches with subagent execution via agentId
  - Parsing recipes (extract tool calls, categorize bash commands, sum token usage)
  - Links to existing analysis scripts (analyze-reviewer-sessions.py, extract-session-metrics.py, analyze-subagents.py)
  - Common waste patterns ranked by impact with detection guidance
  - Gotchas table for structural traps (content type variance, compaction agents, model field location)

## [1.40.0] - 2026-03-01

### Added

- **New `using-figma` skill** — Structured workflow for translating Figma designs into production code with high fidelity. Based on deep analysis of 4 real CIAB-admin sessions that identified 8 systematic anti-patterns causing design mismatches. Key features:
  - **5-phase workflow** (Survey → Specification → Component Tree → Implementation → Validation) that mandates building a structured mental model before coding
  - **Design Specification Documents** — Intermediary data state between raw Figma responses and code, persisting across context compressions
  - **9 iron rules** derived from observed failures: always call `get_variable_defs`, never use screenshots as sole implementation source, always use project tokens, never batch Figma with other tool providers, etc.
  - **Project-agnostic core** with `.claude/figma-config.json` configuration for project-specific token mappings (Figma → project design system)
  - **Cross-session caching** for token definitions, token mappings, and node hierarchies
  - **Bundled Python scripts** for parsing large Figma responses: `figma-parse-nodes.py` (metadata hierarchy) and `figma-extract-specs.py` (design context specifications)
  - **Design spec template** for consistent specification documents

## [1.39.1] - 2026-02-28

### Fixed

- **ReviewOutputBuilder API hallucination** — Bootstrap Section 3 now includes a complete usage example with all core methods (add_issue, add_positive, set_files_reviewed, set_confidence, save). Previously only showed the constructor, causing all 14 agents to hallucinate wrong method names on first write attempt (~3.2 wasted calls/agent).
- **Bootstrap output size cascade** — Scope output exceeding 15KB is now written to scoped-diff.patch and truncated inline with read instructions. Prevents the persistence cascade that wasted 2-3 calls per large PR session.
- **Post-write verification reads** — Bootstrap now instructs agents to trust save()'s return value, eliminating unnecessary Read calls per agent per session.
- **Dead-code Step 0 unconditional PHP check** — Bootstrap injects DYNAMIC_DISPATCH_RISK computed from file extensions; dead-code-reviewer skips the PHP hook grep when no PHP files are in scope (~1 wasted call in 50% of sessions).
- **Output filename mismatch** — ReviewOutputBuilder.save() now writes `{reviewer}-review.json/.md` matching the convention documented in bootstrap and shared protocol. Previously wrote `{reviewer}.json/.md`, causing filename mismatches.

## [1.39.0] - 2026-02-28

### Changed

- **history-insights-reviewer efficiency overhaul** — Based on deep analysis of 3 session transcripts showing 60-70 git commands per run. Key changes:
  - Bootstrap now provides merge-base-correct diffs (eliminates 8-11 redundant `git diff` commands per session)
  - All keyword/pickaxe searches use `--first-parent --since="12 months ago"` (10-100x faster on repos with many branches)
  - Pickaxe split into two phases: find SHAs first (no `-p`), then selective `git show` (major token reduction)
  - Fixed `-S`/`-G` confusion: `-S` for literal strings, `-G` for regex (eliminates ~half of 19% pickaxe failure rate)
  - Added `git blame` as supplementary discovery tool
  - Added explicit parallel branch detection as Phase 1.5 (elevates agent's most unique capability)
  - Added soft ~35 command budget and patterns-reviewer dedup
  - Pre-computed file history in bootstrap (last 15 commits per changed file)
  - Expected savings: ~35% token reduction (5.3M → ~3.5M avg), ~35% runtime reduction (5m35s → ~3m30s)

## [1.38.1] - 2026-02-28

### Changed

- **Model demotion: 4 agents from Opus (inherit) to Sonnet** — architecture-reviewer, wp-architecture-reviewer, patterns-reviewer, and history-insights-reviewer pinned to Sonnet instead of inheriting the parent session model (typically Opus). Cost-normalized analysis showed these agents consumed a disproportionate share of the token budget at Opus pricing (5x Haiku). pr-reviewer and a11y-reviewer stay on Opus (inherit). Expected savings: ~22% of cost-normalized budget.

## [1.38.0] - 2026-02-28

### Added

- **Adaptive agent dispatch (Step 3.6)** — LLM triage step between file-type preflight and agent dispatch. Six conditional agents (security, dead-code, architecture, wp-architecture, performance, a11y) are now evaluated against per-agent dispatch criteria using the diffstat and commit messages. Agents that don't match criteria are skipped with `STATUS=SKIPPED_TRIAGE` signal, reducing wasted token budget by ~20-30% without losing confirmed findings. Triage defaults to DISPATCH when in doubt to maintain safety.

### Fixed

- **Reconciliator missing dead-code and go-tests agents** — The reconciliator's `agent_names` list was missing `dead-code` and `go-tests`, causing their findings to be silently dropped from reconciled summaries.
- **pr-review.md stale agent count** — Updated Step 8 override from hard-coded "12 agents" to "all eligible agents with triage."

## [1.37.1] - 2026-02-28

### Changed

- **architecture-reviewer — Narrow scope to eliminate patterns-reviewer overlap** — Added explicit exclusions for code duplication, structural inconsistency, and consolidation opportunities (all handled by patterns-reviewer). Added -20 confidence reducer for findings that primarily recommend "extract shared code" or "align with existing implementation." Updated collaboration section to clarify the boundary. Based on overlap analysis showing 8 co-reported findings (all duplication/consistency) and architecture-reviewer's 50% unique contribution rate — the worst in the pipeline.

## [1.37.0] - 2026-02-28

### Added

- **reviewer-protocol — Three precision guardrails from ingest validation analysis (313 findings, 29 sessions)** — (1) "Bug or Preference?" self-check gate for LOW/MEDIUM findings to reduce STYLE/PREFERENCE noise (15.7% of output); (2) Factual-claim verification mandate requiring Read tool confirmation before reporting what code does/doesn't do (addresses 47% of false positives); (3) STOP escalation pattern before every `add_issue()` call requiring file+line scope verification (addresses 6.4% OUT OF SCOPE rate). All three changes are additive to the existing 4-point verification checklist.
- **wp-architecture-reviewer — Anti-FP checks for framework conventions** — Three rules addressing the agent's 13% FP rate: verify against type definitions before flagging APIs, developer-only strings don't need i18n, and clean removals are not dead code.
- **architecture-reviewer — WordPress context dampener** — Conditional -10 confidence for abstract SOLID opinions in WordPress code without concrete defects, addressing the precision drop from 80% (Go) to 53.6% (WordPress).
- **history-insights-reviewer — Relevance gate** — Insights must connect to code being changed in the PR; "good to know" findings from unrelated areas get INFO severity or are dropped.
- **ingest-code-review — Source inference rule** — Step 2 now requires inferring agent source from filename when no explicit field is present, eliminating the 25.6% UNKNOWN attribution gap.

## [1.36.0] - 2026-02-28

### Added

- **patterns-reviewer agent — Pattern relevance improvements** — Four changes to reduce false positives and improve finding quality: (1) RULE 1: 3+ independent usage gate — patterns need 3+ instances to be reported as "established," with exceptions for authoritative locations and small codebase adjustment; (2) Proximity confidence modifiers — same-module patterns get +15 confidence, distant patterns get -15, using the existing confidence system instead of a separate score; (3) Staleness check step — new Step 5 in the discovery process uses `git log -S` to detect actively-adopted vs declining patterns, with confidence reduction for dying patterns; (4) Contextual verdict qualifiers — verdicts now require usage counts, area context, and freshness indicators in descriptions.

## [1.35.3] - 2026-02-28

### Fixed

- **a11y-reviewer agent — Wired into all dispatch and documentation locations** — Added a11y-reviewer as agent #13 in both `/full-code-review` and `/code-review` dispatch tables, added `a11y` to review-reconciliator's agent_names list and file tree, added to pirategoat-tools and root README agent tables (17→18 agents), added to TestDeriveReviewerName parametrize list, updated test_commands dispatch count (12→13), and removed stale hardcoded agent counts from TESTING.md.

## [1.35.2] - 2026-02-28

### Changed

- **`a11y-reviewer` agent — Prompt engineering optimization** — Applied 10 research-backed patterns: Affirmative Directives (setup and confidence scoring), STOP Escalation (AP-01/AP-02 metacognitive checkpoints after RULE 0), Contrastive Examples (WRONG/RIGHT code for AP-01, AP-02, AP-07), UX-Justified Defaults (keyboard traps, tabindex, aria-hidden impact), Error Normalization ("Insufficient Context Is Normal" section), Conditional Sections (WordPress-only markers on P2 items and AP-14/AP-16), Completeness Checkpoint (keyboard protocol with named steps: Reach/Activate/Escape/Understand/Return), Numbered Rule Priority (assigned AP-17/18/19 to unnumbered entries, sorted table by severity), Hint-Based Guidance (attention primers before each sweep), Affirmative confidence scoring.

## [1.35.1] - 2026-02-28

### Changed

- **`accessible-frontend-dev` skill — Prompt engineering optimization** — Applied 10 research-backed prompt engineering patterns: Identity Establishment (role priming), Priority System legend (P0/P1 explained), STOP Escalation for `<div onClick>` anti-pattern, Scope Limitation (explicit boundaries), UX-Justified Defaults (disabled state, focus indicators, high contrast rationale), Error Normalization (pragmatic a11y debt guidance), Contrastive Examples (CORRECT/INCORRECT code for top 2 focus bugs), Affirmative Directives (converted 4 negative rules to affirmative framing), Confidence Building (trust decision tree outputs), and Conditional Sections (WordPress/Gutenberg skip instruction).

## [1.35.0] - 2026-02-27

### Added

- **`accessible-frontend-dev` skill — Decorative Content Rendering rules** — Decision tree for choosing between pseudo-elements, inline SVG, `mask-image`, and text nodes. Covers screen reader behavior of `::before`/`::after` (announced per W3C AccName spec), text selection/clipboard exclusion, translation tool immunity, and DOM-walker invisibility. Rule: never put Unicode symbols in CSS `content` for icons.
- **`accessible-frontend-dev` skill — CSS-First Presentational Concerns** — Prefer CSS mechanisms over JS runtime checks: `:dir(rtl)` over `isRTL()`, logical properties for layout, media queries for motion/color-scheme/forced-colors. Includes "workaround smell" heuristic for recognizing when an approach fights the platform.
- **`accessible-frontend-dev` skill — WordPress Twemoji platform hazard** — Documents how Twemoji's `MutationObserver`-based DOM walker replaces Unicode characters in text nodes (including arrows, symbols, not just emoji faces). Correct approach: render decorative symbols via `::after` or SVG to avoid interference entirely.
- **`accessible-frontend-dev` skill — Motion & Animation rules** — `prefers-reduced-motion` media query requirement, reduced-motion alternatives that preserve meaning, auto-playing content pause/stop control (WCAG 2.2.2).
- **`accessible-frontend-dev` skill — High Contrast & Forced Colors rules** — Windows High Contrast Mode testing guidance, `currentColor` for SVG fills, `outline` over `box-shadow` for focus indicators, border/outline state indicators.
- **`accessible-frontend-dev` skill — Keyboard Shortcuts Declaration** — `aria-keyshortcuts` attribute guidance for components with non-standard keyboard shortcuts.
- **`accessible-frontend-dev` skill — Skip Navigation** — Skip-to-content link requirement for SPA views with repeated navigation, WordPress target selectors.
- **`component-patterns.md` — External Link / Opens in New Tab pattern** — Security (`rel="noreferrer noopener"`), accessible name patterns, icon rendering (prefer `mask-image` or SVG, avoid Unicode text nodes), RTL via `:dir(rtl)::after`, hash-link edge case.
- **`component-patterns.md` — Treeview pattern** — Full APG Tree View pattern with `role="tree"`/`role="treeitem"`/`role="group"`, arrow key navigation, expand/collapse, and structure example.
- **`component-patterns.md` — Drag-and-Drop pattern** — Accessible drag-and-drop with keyboard alternatives (action mode, move buttons), live announcements for grab/move/drop/cancel, and implementation skeleton.
- **`a11y-reviewer` agent — P1 checklist items** — `prefers-reduced-motion`, forced-colors focus indicators, `aria-disabled` vs HTML `disabled`, `aria-keyshortcuts` presence, Unicode symbols in CSS `content`, decorative text node clipboard leakage, JS RTL checks for presentational concerns.
- **`a11y-reviewer` agent — P2 checklist items** — Skip navigation, drag-and-drop keyboard alternative, treeview arrow key navigation, Twemoji-vulnerable decorative symbols in WordPress context.
- **`a11y-reviewer` agent — Anti-pattern heuristics AP-10 through AP-16** — Motion without reduced-motion fallback, focus indicator lost in high contrast, inaccessible drag-and-drop, Unicode symbols in pseudo-element content, `wp-exclude-emoji` workaround smell, JS RTL for presentational styling.

## [1.34.0] - 2026-02-27

### Added

- **`accessible-frontend-dev` skill** — Comprehensive accessibility skill for writing WCAG 2.2 AA-compliant frontend code. Includes decision trees (ARIA vs HTML, focus strategy, announcements, disabled state), universal rules distilled from 450+ Gutenberg a11y bug fixes, component pattern quick reference, and Gutenberg infrastructure reference (`useConstrainedTabbing`, `useFocusReturn`, `speak()`, etc.). Heavy reference file covers 13 APG component patterns with full ARIA, keyboard, and focus specifications.
- **`a11y-reviewer` agent** — Accessibility-focused code review agent (the 14th review agent). Runs P0/P1/P2 checklists against changed files, applies 13 anti-pattern detection heuristics from real Gutenberg bugs, confidence-scores each finding, and follows the "keyboard-only thought experiment" methodology. Integrates with the existing review orchestration system via bootstrap script and `a11y` domain in review-scope.

## [1.33.4] - 2026-02-27

### Changed

- **`/copy-as` strips review process artifacts from PR comments** — PR review comments now omit internal review methodology (agent counts, tool names), finding IDs (F1, F3+F4), and label-like prefixes (Verdict:, Approach:, Suggestion:). Uses descriptive headings and natural prose instead of machine-readable references.

## [1.33.3] - 2026-02-27

### Changed

- **`/copy-as` dual-audience PR content** — When copying for PR descriptions or review comments, content is now structured with a human-scannable recap (3-5 bullets, ~100 words) followed by detailed AI-friendly context below a separator. Includes contrastive before/after example showing wall-of-text vs. recap+details structure. Explicitly reconciles this with the "default to human-readable" rule via exception clause. PR review comments specifically strip contextually obvious details (PR number, verdict, restating what the PR does, filler praise) and use a direct peer-to-peer voice.

## [1.33.2] - 2026-02-27

### Changed

- **`gemini-reviewer` forces Gemini 2.5 Pro** — All CLI invocations now pass `-m gemini-2.5-pro` instead of relying on auto-routing, ensuring consistent model selection for code review quality.

## [1.33.1] - 2026-02-26

### Changed

- **`/ingest-code-review` prompt strengthened** — Added pre-work context sentence establishing the 6-call loop before step 1 runs; added explicit STOP escalation when the script exits with an error; replaced the passive loop-continuation paragraph with a labeled affirmative-directive numbered list.

## [1.33.0] - 2026-02-26

### Changed

- **`/ingest-code-review` uses step-by-step prompt injection** — Replaced single-pass instructions with a 6-step script-driven workflow (`scripts/ingest-code-review.py`). Claude now enforces factored verification in steps 4-5: it generates falsification questions per finding, reads the actual code with the Read tool, then answers questions independently before judging a finding. Grounded in Chain-of-Verification (Dhuliawala et al., 2023).

## [1.32.4] - 2026-02-26

### Changed

- **`/code-review` auto-ingests findings** — Added Step 7 that automatically invokes `pirategoat-tools:ingest-code-review` after the reconciliator finishes, matching the behaviour added to `/full-code-review` in 1.32.3.

## [1.32.3] - 2026-02-26

### Changed

- **`/full-code-review` auto-ingests findings** — Added Step 7 that automatically invokes `pirategoat-tools:ingest-code-review` after the reconciliator finishes. Previously users had to manually run `/ingest-code-review` as a follow-up. The ingest step now runs back-to-back with Step 6 without waiting for user input.

## [1.32.2] - 2026-02-26

### Fixed

- **`/copy-as` content quality** — Two Step 2 refinements: (1) default to human-readable form — extract prose/structured output rather than raw data (JSON, logs, tool output) unless explicitly requested; (2) no hard line breaks — output each paragraph/list item/heading as a single continuous line so paste targets reflow correctly.

## [1.32.1] - 2026-02-25

### Added

- **`/copy-as` P2/Gutenberg HTML format** — New `p2` target for the `/copy-as` command. Converts markdown to semantic HTML (15 element rules) and uses a Swift NSPasteboard script to set both `public.html` and `public.utf8-plain-text` on the clipboard. Gutenberg auto-converts pasted HTML to blocks, so users can Cmd+V directly into P2 posts and comments without needing Cmd+Shift+V for plain text paste.

## [1.32.0] - 2026-02-25

### Added

- **`/copy-as` command** — Copies content to clipboard formatted for the target destination. Defaults to standard markdown (pass-through); when `slack` is specified, converts to Slack's mrkdwn syntax via a 14-rule conversion checklist — bold/italic syntax differences (`**` → `*`, `*` → `_`), link inversion (`[text](url)` → `<url|text>`), heading removal (→ bold text), table conversion (→ preformatted code blocks), strikethrough (`~~` → `~`), code block language identifier stripping, HTML tag removal, and special character escaping with explicit code span protection. Prompt-engineered with Identity Establishment, Scope Limitation, Completeness Checkpoint Tags, Emphasis Hierarchy (RULE 0), Contrastive Examples, and Confidence Building patterns.

## [1.31.1] - 2026-02-24

### Changed

- **`/pr-review` composition refinements** — Rewrote command to compose existing skill and commands instead of duplicating content. PR context gathering delegates to the pr-reviewing skill, agent dispatch uses `/full-code-review` (all 12 agents regardless of PR size), and validation uses `/ingest-code-review`. Applied prompt engineering patterns: RULE 0 emphasis for autonomy constraint, pipeline overview, compressed redundant step enumeration.

## [1.31.0] - 2026-02-24

### Added

- **`/pr-review` command** — End-to-end PR review pipeline that runs without interruption. Gathers full PR context (details, issue, review state), dispatches all 12 review agents in parallel, reconciles findings, validates each finding against actual code (filtering false positives and out-of-scope items), and saves a comprehensive review document to `/tmp/pr-review-<PR_NUMBER>/review-report.md`. Combines the pr-reviewing skill, full-code-review, and ingest-code-review workflows into a single non-interactive command.

### Changed

- **Tiered model assignments for reviewer agents** — Assigned models based on reasoning complexity to reduce cost and latency. Orchestration and pattern-matching agents (gemini-reviewer, codex-reviewer, technical-writer, go-tests-reviewer) downgraded to haiku. Checklist-driven agents (security, performance, dead-code, tests-mutation, php/js/e2e-tests reviewers) set to sonnet. Deep-reasoning agents (pr-reviewer, architecture, wp-architecture, patterns, history-insights) remain on inherit (Opus).

## [1.30.0] - 2026-02-23

### Fixed

- **review-scope.py merge-base always active** — Merge-base range rebasing now happens unconditionally when a merge-base exists, not only when the branch is >10 commits behind. Previously, any divergence from the base branch (even 1 commit) could cause review agents to flag unrelated files from trunk. The `STALE_BRANCH_THRESHOLD` now only controls the advisory warning message. `--no-merge-base` remains as an escape hatch. Text output now shows `RANGE_REBASED` even for non-stale branches.

### Added

- **pr-reviewing skill merge-base anchoring** — Step 1 now computes `MERGE_BASE` after fetching branches. Steps 7 and 8 use `${MERGE_BASE}..HEAD` for all diffs and agent dispatch (replacing `<baseRefName>...<headRefName>`). Agent context template includes an authoritative changed files list with a constraint that agents must only review listed files (defense-in-depth against wrong ranges).
- **Expanded noise filters** — `package-lock.json`, `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `go.sum`, `.po` translation files, `.yarn/` directory, `__pycache__/` directory, coverage directories (`coverage/`, `.nyc_output/`, `htmlcov/`), `.cache/` directory, `tsconfig.tsbuildinfo`, `.eslintcache`, and `.stylelintcache`.
- **test_review_scope.py** — 57 new tests: pure function unit tests (`rebase_range_to_merge_base`, `detect_base_ref`, `count_diff_lines`, `filter_noise`, `filter_domain`) and integration tests for the merge-base gating fix (non-stale rebase, `--no-merge-base` escape hatch, stale warning decoupling, text/JSON output format, range rewriting).

### Changed

- **test_domain_routing.py** — Updated `test_non_stale_branch_no_range_rebase` → `test_non_stale_branch_still_rebased` to match new unconditional merge-base behavior.

## [1.29.1] - 2026-02-23

### Changed

- **browser-interaction skill restructured for clarity** — Consolidated 3 entry-point sections (Prerequisites, Quick Start, MCP Detection) into single Prerequisites. Merged RULE 0 with Common Operations so workflow loop and code examples appear together. Streamlined RULE 1 with action-first decision table (removed dot graph, demoted non-actionable explanation). Moved Chrome DevTools Profile Locations to Reference section at bottom. Removed vague "When to Use" section (covered by frontmatter). 167 → 118 lines, same content.

## [1.29.0] - 2026-02-23

### Added

- **browser-interaction token efficiency guidance** — New RULE 1 with decision flow for choosing snapshots vs screenshots, token cost formula (`width × height / 750`), comparison table of snapshot vs screenshot trade-offs, and warning about heavy-navigation pages where snapshots can exceed screenshot costs. Updated code examples to prefer element-targeted screenshots (`uid` parameter) over full-page captures.
- **token efficiency analysis doc** — Research analysis documenting the investigation into image tokenization behavior, grayscale/format experiments, and findings that led to the skill update.

## [1.28.1] - 2026-02-22

### Changed

- **software-architecture skill library** — Compressed 16 reference files from 21,772 to 2,886 lines (86.7% reduction) for AI agent token efficiency. Removed pattern history, UML diagrams, metaphors, quotes, generic OOP examples (Java/C#/Python), "further reading" sections, and definition paragraphs that Claude already knows from training. Kept all WordPress/PHP and JS/TS/React code examples, When to Use / When NOT to Use decision criteria, Common Mistakes with WRONG/RIGHT pairs, and pattern relationship maps. Added JS/TS examples to Composite (React component tree), Decorator (HOC), Facade (module re-export), and Factory (React component factory). Fixed patterns/README.md to only reference files that exist (removed 18 aspirational entries for unwritten pattern files). All SKILL.md routing table headings verified intact.

## [1.28.0] - 2026-02-20

### Added

- **go-tests-reviewer agent** — New specialized reviewer for Go test quality: standard `testing` package patterns, table-driven tests, subtests, test helpers (`t.Helper()`), `httptest`, interface-based mocking, benchmarks, fuzz testing, and bubbletea TUI testing. Dispatched as the 12th reviewer agent (13 total with dead-code) in `/code-review` and `/full-code-review`.
- **go-testing-patterns skill** — User-facing skill with Go assertion quick reference, red flags table, table-driven test template, and interface-based mocking guidance.
- **go-testing-patterns.md reference** — ~420-line deep reference covering the full Go testing ecosystem: table-driven tests, subtests, `TestMain`, cleanup/isolation (`t.TempDir`, `t.Setenv`, `t.Cleanup`), parallel tests, `httptest`, interface-based mocking, benchmarks, fuzz testing, bubbletea TUI testing, `testdata/` conventions, race detection, and build tags.
- **go-tests domain** — `review-scope.py` now recognizes `_test.go` files as the `go-tests` domain for preflight filtering and scope discovery. Also added `_test.go` to the `dead-code` domain exclude pattern.
- **Go test fixtures** — `go-test-only.diff` and `go-source.diff` fixtures for domain routing tests.
- **Test updates** — All 3 test files updated: `go-tests-reviewer` in `TEST_AGENTS` and name derivation, agent count 11→12, `go-tests` domain in `ALL_DOMAINS` and all routing matrix entries (11 fixtures × 11 domains). 352 tests pass (up from 320).

## [1.27.0] - 2026-02-15

### Added

- **Stale branch detection** — `review-scope.py` now detects when a feature branch is far behind the base branch (>10 commits) and automatically rebases the diff range to the merge-base (common ancestor). This excludes unrelated trunk files from leaking into review scope. Adds `BRANCH_FRESHNESS:` section to preflight output with `AHEAD`, `BEHIND`, `IS_STALE`, `MERGE_BASE`, and `RANGE_REBASED` fields. JSON output includes `branch_freshness` dict. New `--no-merge-base` flag disables the automatic adjustment.
- **full-code-review command** — Step 3.5 now parses `BRANCH_FRESHNESS` from preflight output, informs the user when scope was adjusted, and suggests rebasing.
- **code-review command** — Step 3.5 adds conditional stale branch check (only acts when `history-insights-reviewer` is dispatched).
- **TestBranchFreshness** — 6 new tests in `test_domain_routing.py`: stale detection, non-stale detection, merge-base range rebasing, `--no-merge-base` bypass, JSON output validation, and non-stale no-rebase.
- **Structural tests** — 3 new tests in `test_commands.py`: stale branch handling in full-code-review, merge-base reference in full-code-review, conditional stale handling in code-review.

## [1.26.2] - 2026-02-13

### Changed

- **dead-code-reviewer agent** — Reframed as "prove reachability" (innocent until proven dead) with stronger evidence requirements. Excludes test files entirely from analysis. Added contrastive examples (correct vs incorrect findings), dynamic dispatch risk assessment (Step 0), universal search template with error handling, categorized false positive checklist (framework callbacks, language magic, dynamic dispatch, build/test infra), worked confidence scoring example, and explicit collaboration boundary rules with handoff signals.

## [1.26.1] - 2026-02-13

### Changed

- **review-scope.py** — Added `--preflight` mode that checks all 10 domains in a single invocation (one `git diff --name-only` call) and outputs `DISPATCH_DOMAINS` / `SKIP_DOMAINS` lists. Supports both text and JSON (`--format json`) output formats.
- **full-code-review command** — Added Step 3.5 (pre-flight scope check) before agent dispatch. Agents whose domain has no matching files are skipped entirely instead of launched and self-exiting. Domain column added to agent table for clarity.
- **code-review command** — Same pre-flight scope check and conditional dispatch changes as full-code-review.
- **test_domain_routing.py** — Added `TestPreflight` class with 8 tests: text/JSON output format, no-domain-required, cross-validation against individual domain checks (all 9 fixtures × 10 domains), dispatch/skip consistency, and all-skip scenarios.

## [1.26.0] - 2026-02-11

### Added

- **dead-code-reviewer agent** — Identifies dead code introduced or exposed by changes: unused functions, unreachable code paths, orphaned imports, unused parameters, and code made obsolete by refactors. Uses `git grep` verification protocol (RULE 0: prove it's dead before reporting) with a comprehensive false positive checklist covering WordPress hooks, magic methods, dynamic dispatch, DI containers, and 17 other dynamic patterns. Categories: `unused-function`, `unused-import`, `unused-variable`, `unused-parameter`, `unreachable-code`, `orphaned-survivor`, `unused-export`, `unused-class`. Dispatched as the 12th reviewer agent in `/code-review` and `/full-code-review`.

## [1.25.0] - 2026-02-09

### Added

- **pr-update command** — Analyzes the current PR branch, discovers relevant artifacts (plans, reviews), respects the project's PR template, generates an accurate description proportional to PR size, validates every claim against the actual diff, and updates the PR after user approval. Supports `gh` and `ghe` (GitHub Enterprise). 8-step protocol: PR detection, branch context, template detection, artifact discovery, draft generation, validation pass, user approval gate, PR update.
- **TestPrUpdate test class** — 12 structural tests for the pr-update command: frontmatter, PR detection, template detection, validation step, approval gate, PR edit, ghe fallback, STOP conditions, brevity calibration, artifact discovery, marketplace registration, and REVIEW_COMMANDS exclusion.

## [1.24.0] - 2026-02-09

### Added

- **code-review command** — Incremental branch-level code review that tracks last reviewed commit and only reviews new changes. Persists state in `.review-state.json` in the output directory. Supports `full`/`reset` arguments to force a full review, and auto-detects rebases to fall back gracefully.
- **ingest-code-review command** — Reads review findings from `/code-review` or `/full-code-review`, validates each finding against the actual diff, filters false positives and out-of-scope noise, and proposes a prioritized action plan.
- **test_commands.py** — Deterministic evals for review command files: frontmatter validation, agent reference cross-checking against marketplace.json, script existence verification, dispatch consistency between commands, and command-specific content checks. 36 test cases.
- **grade_review_state grader** — Validates `.review-state.json` files: required fields, SHA format, positive review count, range separator. 8 test cases in test_graders.py.

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

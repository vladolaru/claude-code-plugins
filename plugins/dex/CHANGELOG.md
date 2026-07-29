# Changelog

## [Unreleased]

### Tests

- Removed `tests/__init__.py` as part of the repo-wide fix for multi-plugin pytest collection collisions (all plugin suites were importable as the same package `tests`); the root `pytest.ini` now pins `--import-mode=importlib`. No runtime change.

## [1.6.0] - 2026-07-23

### Added

- Codex plugin packaging and seven generated command-skill adapters, all
  derived from the canonical Claude Code command files.

### Changed

- User confirmation guidance is host-neutral so the shared knowledge-capture
  workflow can use the input mechanism available in Claude Code or Codex.

### Fixed

- The shared `knowledge-capture` skill is now surfaced into the Codex skill
  set. Every `/dex:*` command delegates host discovery (CLAUDE.md/`.claude`
  vs AGENTS.md/`.ai`) to it, so without it Codex would fall back to
  Claude-specific paths; surfacing it lets Codex resolve the active knowledge
  directory and instructions file correctly.
- The generated `sharpen` adapter assigns `CODEX_PLUGIN_ROOT` in its shell
  example instead of referencing the unexported variable.
- Project discovery now recognizes a direct root `AGENTS.md` (a Codex-only
  project with no `CLAUDE.md`), resolving `ai_dir` to `.ai` instead of treating
  the knowledge infrastructure as missing.

## [1.5.3] - 2026-03-02

### Fixed

- **sharpen command:** Use `${CLAUDE_PLUGIN_ROOT}` (expanded by CC for commands) instead of `${PLUGIN_ROOT}` (not available) for script path resolution

## [1.5.2] - 2026-02-24

### Changed

- Grok router: renumbered Step 2.5 (Graduation Check) to Step 3, Step 3 (Delegate) to Step 4 — clean integer step numbering (Format Strictness)
- Sharpen command: tightened `${PLUGIN_ROOT}` resolution with explicit existence check for `analyze-subagents.py` and graceful skip on missing script (Error Normalization)
- Knowledge-capture skill: added Write Failure Recovery guidance — on file write failure, display intended content for manual save instead of undefined behavior (Error Normalization)

## [1.5.1] - 2026-02-24

### Added

- Deterministic `ai_dir` derivation — CLAUDE.md → `.claude/`, AGENTS.md → `.ai/`, resolved in Project Discovery step 3 and used for all knowledge directory paths
- Variable Substitution note — all commands substitute resolved `ai_dir` and instructions filename in paths and user-facing messages without per-command edits
- Migration mismatch detection — when `ai_dir` is `.ai` but knowledge exists in `.claude/docs/`, flag for init and status to surface
- `/dex:init` migration step (Step 3.5) — offers to move `.claude/docs/` → `.ai/docs/` when mismatch is detected
- `/dex:status` mismatch warning — reports when knowledge directory doesn't match the resolved `ai_dir`, suggests running `/dex:init`

## [1.5.0] - 2026-02-24

### Added

- AGENTS.md indirection support in Project Discovery — if CLAUDE.md is a symlink to AGENTS.md or contains only `@AGENTS.md`, all operations target AGENTS.md instead

## [1.4.1] - 2026-02-15

### Changed

- Grok router: added routing digraph showing full decision flow from start to delegation — makes classification, graduation, and delegation paths unambiguous
- Knowledge-capture skill: unified promotion decision and budget enforcement into a single digraph — agents trace one graph instead of cross-referencing two separate sections
- Sharpen command: added early-exit digraph showing all stop points and conditional sub-agent analysis — prevents reading past termination branches

### Tests

- Added 9 tests for `_compute_duration_seconds` in analyze-subagents — covers Z-suffix, fractional seconds, empty inputs, reversed timestamps, garbage input, large durations

## [1.4.0] - 2026-02-15

### Changed

- Pattern format: renamed "When NOT to apply" to "Alternatives" with affirmative framing — prescribes what to do instead (e.g., "When X, prefer Y") rather than negative instructions LLMs handle poorly
- Pattern promotion: always offered now, not conditional on rule-worthiness — unpromoted patterns are invisible to agents, defeating compound engineering
- Knowledge-capture skill: promotion guidance now documents patterns-always-promote policy separately from learnings' conditional promotion
- Grok router: learning-vs-pattern heuristic now defaults to learning when ambiguous — most insights start as learnings and can graduate to patterns
- Grok router: added graduation check (Step 2.5) that offers to upgrade a learning to pattern when reusability signals are detected, keeping the default as "keep as learning" to avoid friction

## [1.3.1] - 2026-02-15

### Changed

- Knowledge-capture skill: stripped bold from Decision Format option labels to match Token Efficiency principles (Format Strictness)
- Knowledge-capture skill: auto-placement step now hints at matching tags/topic against section headings (Hint-Based Guidance)
- Knowledge-capture skill: sharpen CORRECT example now demonstrates all three quality checks including Non-obvious (Completeness Checkpoint)
- Init command: removed duplicated Project Discovery steps — delegates to skill like all other commands (Token Compression)
- Init command: "already set up" branch now continues to capture directive check instead of premature stop (Conditional Sections)
- Grok router: confirmation step now shows "Detected:" summary so user can validate classification (HITL Pre-Execution Checkpoint)
- Grok router: removed redundant per-option `(Recommended)` labels and `*(only if ...)*` annotations — standalone instruction already controls behavior (Token Compression)
- Status command: freshness warnings reordered most-severe-first with explicit "first matching row" precedence (Format Strictness)
- Status command: vague `etc.` in CLAUDE.md exclusion list replaced with named category — "dependency and build directories" (Category-Based Generalization)
- Document formats optimized for AI agent token efficiency: plain metadata labels (`Date:` not `**Date:**`), bare path references, direct `file:line` notation
- Added Token Efficiency design principle section codifying bare paths, plain metadata, direct references, and no prose filler
- Fixed extraction flow to use bare paths (`Full details: path`) instead of markdown links, matching the skill's own promoted rule format
- Extraction quality checks restructured as `<extraction_quality_checklist>` completeness checkpoint (Completeness Checkpoint Tags pattern)
- Added `<pre_extraction_analysis>` step before drafting knowledge documents (Pre-Work Context Analysis pattern)
- Audit log format stripped of bold markers to match Token Efficiency principles (Format Strictness)
- Sharpen quality section now explicitly names standard checks before adding sharpen-specific ones (Hint-Based Guidance pattern)
- Removed redundant post-example explanations — contrastive examples teach through demonstration (Token Compression)
- Non-obvious check reframed with affirmative language (Affirmative Directives pattern)
- Commands now reference skill's `<pre_extraction_analysis>` instead of vague "re-read" prose (Pre-Work Context Analysis)
- Added Error Normalization to learn, pattern, and research commands — stop gracefully when nothing extractable exists
- Fixed bold metadata in sharpen example (`**Tags:**` → `Tags:`) and research write step (`**Status:**` → `Status:`) for Token Efficiency consistency
- Fixed scaffolding in learn, pattern, and sharpen to include `research/` directory (was only listed in research and init)
- Promotion criteria in learn and pattern now reference the skill's **When to Suggest Promotion** section to prevent divergence
- Fixed circular self-reference in status freshness warning ("consider a review pass with /dex:status" → actionable guidance)
- Grok router: sharpen routing check moved before classification (STOP Escalation) with domain-vs-operational knowledge distinction
- Grok router: replaced vague "Re-read" with signal-oriented scan (Pre-Work Context Analysis)
- Grok router: added Error Normalization for conversations with nothing to capture
- Grok router: added Pattern vs. Decision disambiguation heuristic (Category-Based Generalization)
- Grok router: restructured overloaded Decision delegation as "overrides" list (Format Strictness)
- Learn command: Rule and Context/Examples drafting hints now specify content and format expectations (Hint-Based Guidance)
- Learn command: extraction quality checklist referenced before user confirmation to reduce edit cycles (Completeness Checkpoint Tags)
- Learn command: "not on narrating" reframed as "focus on the behavioral change" (Affirmative Directives)
- Pattern command: Pattern, When to apply, and Reference implementation drafting hints strengthened with format/content expectations (Hint-Based Guidance)
- Pattern command: extraction quality checklist referenced before user confirmation (Completeness Checkpoint Tags)
- Pattern command: boundary emphasis reframed as consequence — "a pattern without boundaries will be misapplied" (Affirmative Directives)
- Research command: Environment hint now emphasizes future-relevance with concrete example format (Hint-Based Guidance)
- Research command: What Works hint specifies evidence types — commands, configs, code (Hint-Based Guidance)
- Research command: extraction quality checklist referenced before user confirmation (Completeness Checkpoint Tags)
- Research command: "not narrating" reframed as three-part empirical structure — tried, evidence, unknown (Affirmative Directives)
- Sharpen command: merged "Step 6.5" into Step 6 — clean 8-step numbering (Format Strictness)
- Sharpen command: "Avoid duplicating" → "Skip already captured", "Re-read" → "Scan" (Affirmative Directives)
- Sharpen command: post-example explanation compressed to one sentence linking to named quality checks (Token Compression)
- Sharpen command: promotion criteria now references skill's **When to Suggest Promotion** section (Hint-Based Guidance)
- Pattern command: confirmation preview now includes "Applies when" summary for informed approval (HITL Pre-Execution Checkpoint)
- Pattern command: "When NOT to apply" hint now names exception categories — simpler alternatives, wrong scale, conflicting constraints (Hint-Based Guidance)

## [1.3.0] - 2026-02-15

### Added

- `/dex:research` command — captures extensive investigation findings (debugging sessions, API explorations, trial-and-error work) with Environment, Status, What Works, What Doesn't Work, Key Findings, Reproduction Steps, and Open Questions sections
- Research Format in knowledge-capture skill with contrastive examples and structured template
- Research as 4th classification type in `/dex:grok` router with disambiguation heuristic (learning if one insight, research if multiple empirical approaches)
- `research/` directory in init scaffolding, status reporting, and project discovery

### Changed

- Promoted Rule Format now uses bare paths (`Details: path`) instead of markdown links (`See [details](path)`) to save tokens in CLAUDE.md

## [1.2.1] - 2026-02-14

### Added

- Sub-agent behavior analyzer (`scripts/analyze-subagents.py`) — reads Claude Code sub-agent JSONL traces and detects behavioral anti-patterns (wrong tool usage, repeated reads, high token consumption, bash overuse)
- `/dex:sharpen` now runs the sub-agent analyzer as Step 2 before main conversation analysis, mapping flagged patterns to Inefficiency Categories for cross-agent visibility

## [1.2.0] - 2026-02-14

### Added

- Proactive capture directive in `/dex:init` — offers to add a CLAUDE.md reminder for future agents to suggest `/dex:grok` after significant debugging or decisions
- Sharpen audit log (`.claude/docs/.sharpen-log.md`) — cross-session memory for `/dex:sharpen` to avoid duplicating previously captured efficiency fixes
- Freshness analysis in `/dex:status` — warns about stale docs older than 90 days and suggests review when most knowledge is outdated

### Changed

- Renamed router command from `/dex:dex` to `/dex:grok` — "deeply understand, then catalog"

## [1.1.0] - 2026-02-14

### Added

- `/dex:sharpen` command — analyzes agent behavior for inefficiencies and captures fixes as project knowledge
- Agent Behavior Analysis section in knowledge-capture skill with inefficiency categories and root cause classification

### Changed

- Optimized sharpen command and agent behavior analysis with prompt engineering patterns: Pre-Work Context Analysis, Scope Limitation, Hint-Based Guidance, Error Normalization, Contrastive Examples, and Category-Based Generalization
- Added sharpen discoverability note to `/dex` router
- Added classification heuristic for learning-vs-pattern ambiguity in router

## [1.0.1] - 2026-02-13

### Changed

- Optimized all commands and shared skill with research-backed prompt engineering patterns: Scope Limitation, Affirmative Directives, Contrastive Examples, STOP Escalation, Pre-Work Context Analysis, Category-Based Generalization, Error Normalization, Hint-Based Guidance, and Format Strictness

## [1.0.0] - 2026-02-13

### Added

- `/dex` command — thin router that auto-classifies knowledge type (learning, pattern, or decision) and delegates to the right handler
- `/dex:init` command — scaffolds `.claude/docs/` knowledge directory with `learnings/`, `patterns/`, `decisions/` subdirectories
- `/dex:learn` command — captures learnings from conversation context with single-confirmation via AskUserQuestion
- `/dex:pattern` command — captures reusable patterns from conversation context with single-confirmation
- `/dex:status` command — knowledge health report showing CLAUDE.md budget, doc counts, and latest/oldest entries
- `knowledge-capture` skill — shared core logic for project discovery, agent-first document formats, CLAUDE.md budget management, and promotion flow
- Inline promotion flow — after capturing a learning or pattern, optionally promote to a one-liner rule in CLAUDE.md
- CLAUDE.md budget enforcement — free under 500 lines, warn 500-550, hard block 550+
- Auto-placement of promoted rules in the most relevant CLAUDE.md section
- First-run scaffolding — auto-detects missing `.claude/docs/` and offers to create it

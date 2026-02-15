# Changelog

## [1.3.1] - 2026-02-15

### Changed

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

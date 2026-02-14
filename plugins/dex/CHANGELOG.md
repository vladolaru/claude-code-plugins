# Changelog

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

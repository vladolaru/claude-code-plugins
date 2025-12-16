# Changelog

All notable changes to the prompt-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2025-12-16

### Changed

- **Major skill restructure**: Replaced two-phase approach with comprehensive 5-phase human-in-the-loop workflow (Phase 0-4)
- **Quote-first evidence grounding**: All technique selections now require quoted trigger conditions from reference document
- **User approval gates**: Added mandatory approval checkpoints between phases to prevent wasted effort
- **Phase 0 triage**: Added complexity assessment to avoid over-engineering simple prompts
- **Open verification questions**: Replaced yes/no confirmation with open-ended questions to surface issues
- **Quality verification**: Added systematic verification step for major changes before final presentation
- **Anti-pattern integration**: Added explicit anti-pattern checking throughout the process

### Updated

- **prompt-engineering.md reference**: Major expansion with Technique Selection Guide table, domain-organized techniques with research citations, stacking/conflict documentation, and comprehensive Anti-Patterns section

### Attribution

Synced with upstream source: https://github.com/solatis/claude-config

## [1.0.0] - 2025-12-11

### Added

- Initial release as standalone plugin (extracted from pirategoat-tools)
- `prompt-optimizer` skill - Two-phase prompt optimization with pattern attribution
- `/optimize-prompt` command - Quick access to prompt optimization
- Embedded prompt engineering reference guide

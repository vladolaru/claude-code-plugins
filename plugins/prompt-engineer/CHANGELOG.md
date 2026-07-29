# Changelog

All notable changes to the prompt-engineer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] - 2026-07-23

### Added

- Codex plugin packaging and a generated `$prompt-engineer:optimize-prompt`
  command adapter.
- Codex metadata that disables implicit invocation of both the shared skill
  and the generated command adapter.

### Changed

- Reference paths use the host-neutral `SKILL_DIR` convention.
- The shared skill description now carries the explicit-request contract
  instead of the Claude-only `disable-model-invocation` frontmatter field.

### Fixed

- The shared `prompt-engineer` skill is now surfaced into the Codex skill set.
  The generated `optimize-prompt` adapter delegates to it, so without it Codex
  could not resolve the skill the command depends on.

## [2.1.1] - 2026-03-28

### Fixed

- **Reference file paths**: Use `${CLAUDE_SKILL_DIR}` substitution for all reference paths so CC resolves them directly instead of searching the filesystem
- **Stale command reference**: Remove non-existent `references/prompt-engineering.md` path from `/optimize-prompt` command (files were split in v2.0.0)

## [2.1.0] - 2025-01-23

### Added

- **Three new reference documents** integrated into the skill for comprehensive prompt engineering coverage:
  - `prompt-engineering-compression.md` - Research-backed techniques for reasoning compression (Chain of Draft, Concise CoT, TALE-EP, Sketch-of-Thought, MARP, Program-of-Thoughts, Focused CoT, Symbolic CoT)
  - `prompt-engineering-hitl.md` - Human-in-the-loop workflow patterns (HULA framework, Plan Review Gates, Iterative Refinement with Feedback, Pre-Execution Checkpoints, Selective Escalation, Human Context Augmentation)
  - `prompt-engineering-subagents.md` - Subagent orchestration techniques (Skeleton-of-Thought, Tree of Thoughts, Least-to-Most, Task Orchestration, Explicit Reflection, Self-Contrast, Anticipatory Reflection, MPSC, LM^2, Multi-Expert Prompting)
- Conditional loading triggers for each new reference based on prompt characteristics
- Updated Quick Reference diagram showing all five reference categories

### Changed

- Reorganized "Required Resources" section with clearer conditional loading guidance for all references

### Attribution

Synced with upstream source: https://github.com/solatis/claude-config

## [2.0.0] - 2025-12-19

### Changed

- **BREAKING: Renamed plugin from `prompt-optimizer` to `prompt-engineer`** to better reflect the skill's comprehensive scope beyond simple optimization
- **Split reference document**: Single `prompt-engineering.md` now split into `prompt-engineering-single-turn.md` (always read) and `prompt-engineering-multi-turn.md` (conditional read for multi-turn/multi-agent flows)
- Updated all internal references to use new plugin and skill name

### Migration

Users must reinstall: `/plugin install prompt-engineer@vladolaru-claude-code-plugins`

The old `prompt-optimizer` name is no longer available.

## [1.1.1] - 2025-12-16

### Fixed

- **Reference file discovery**: Added explicit path resolution instructions so Claude can reliably find the prompt-engineering.md reference file, with Glob fallback

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

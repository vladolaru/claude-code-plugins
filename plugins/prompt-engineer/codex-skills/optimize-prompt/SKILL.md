---
name: optimize-prompt
description: "Optimize a prompt or file (skill, CLAUDE.md, agent) using the prompt-engineer skill"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/optimize-prompt.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


Use the prompt-engineer skill to optimize the following:

${CODEX_SKILL_ARGUMENTS}

If a file path is provided, read the file first and optimize its contents. Common targets:
- Skills (SKILL.md files)
- Agent definitions (agents/*.md)
- CLAUDE.md or memory files
- Any system prompt or instruction file

Apply ALL prompt engineering patterns from the skill's reference files. For EACH change, specify EXACTLY which technique(s) you used:
- Pattern name (e.g., "Progressive Disclosure", "Emphasis Hierarchy")
- Why this pattern applies here
- Expected behavioral impact

CRITICAL: Changes without pattern attribution = task incomplete.

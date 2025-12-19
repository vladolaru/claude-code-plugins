---
description: Optimize a prompt or file (skill, CLAUDE.md, agent) using the prompt-engineer skill
---

Use the prompt-engineer skill to optimize the following:

$ARGUMENTS

If a file path is provided, read the file first and optimize its contents. Common targets:
- Skills (SKILL.md files)
- Agent definitions (agents/*.md)
- CLAUDE.md or memory files
- Any system prompt or instruction file

Apply ALL prompt engineering patterns from the skill's `references/prompt-engineering.md` file. For EACH change, specify EXACTLY which technique(s) you used:
- Pattern name (e.g., "Progressive Disclosure", "Emphasis Hierarchy")
- Why this pattern applies here
- Expected behavioral impact

CRITICAL: Changes without pattern attribution = task incomplete.
# prompt-engineer

Human-in-the-loop prompt optimization with evidence-grounded pattern attribution using proven prompt engineering techniques.

**Current Version:** 2.0.0

---

## What It Does

Optimizes prompts for Claude (and other LLMs) through a systematic 5-phase workflow:

**Phase 0:** Complexity triage - avoid over-engineering simple prompts
**Phase 1:** Evidence-grounded analysis - identify issues with quoted references
**Phase 2:** Technique selection - apply proven patterns from research
**Phase 3:** Optimization - generate improved version
**Phase 4:** Verification - validate quality before presenting

**Key feature:** Human approval gates between phases prevent wasted effort.

---

## Installation

### Add Marketplace
```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugin
```bash
/plugin install prompt-engineer@vladolaru-claude-code-plugins
```

---

## Usage

### Optimize a Prompt

```bash
/optimize-prompt path/to/prompt.md
```

Or invoke the skill directly:
```bash
# Claude will load the prompt-engineer skill
```

### Example Workflow

**Input:** Your current prompt file

**Process:**
1. **Triage:** "Is this prompt complex enough to need optimization?"
2. **Analysis:** Identify issues with quoted evidence from research
3. **Selection:** Choose proven techniques (Chain-of-Thought, XML tags, etc.)
4. **Optimization:** Generate improved version
5. **Verification:** Quality check before presenting

**Output:** Optimized prompt with technique attribution and explanation

---

## Features

### Evidence-Grounded Analysis
- All technique selections require quoted triggers from reference document
- No guessing or speculation
- Research-backed recommendations

### Human-in-the-Loop Gates
- Approval required between major phases
- Prevents wasted effort on wrong direction
- User can course-correct early

### Comprehensive Reference Library
Embedded research on:
- 50+ prompt engineering techniques
- Anti-patterns to avoid
- Technique stacking and conflicts
- Domain-specific applications
- Research citations

### Quality Verification
- Open-ended verification questions
- Surfaces potential issues before finalization
- Ensures major changes are improvements

---

## Prompt Types Supported

### Agent Prompts (Claude Code)
- Skills (SKILL.md files)
- Agents (agent definitions)
- Commands (slash commands)
- CLAUDE.md files

### API Prompts
- System prompts
- User prompts
- Multi-turn conversations

### General Prompts
- Any text prompt for Claude or other LLMs

---

## Optimization Techniques

**Structural:**
- Chain-of-Thought prompting
- XML tags for clarity
- Few-shot examples
- Role prompting

**Output Control:**
- Output formatting templates
- JSON mode with schemas
- Length control

**Quality:**
- Self-consistency checks
- Reasoning steps
- Anti-pattern avoidance

**Advanced:**
- Multi-turn strategies
- Tool use patterns
- Context management

See embedded reference library for complete catalog.

---

## Key Principles

### 1. Quote-First Evidence
Before recommending a technique, quote the trigger condition from research:

> "Use Chain-of-Thought when: The task requires multi-step reasoning"

Then apply technique if trigger matches.

### 2. Avoid Over-Engineering
Phase 0 triage prevents optimizing prompts that don't need it:
- Simple, working prompts stay simple
- Optimization only when complexity justifies it

### 3. Human Approval Gates
User approves before:
- Moving from analysis to optimization
- Finalizing optimized version
- Can stop or redirect at any phase

### 4. Anti-Pattern Checking
Actively check for and remove:
- Anthropomorphization
- Unnecessary politeness
- Vague instructions
- Conflicting directives

---

## When to Use

**Good candidates for optimization:**
- Prompts getting inconsistent results
- Complex multi-step workflows
- Prompts needing better structure
- Agent definitions for production use
- API prompts with quality issues

**Skip optimization for:**
- Simple, working prompts
- One-off queries
- Exploratory prompts
- Prompts that already follow best practices

---

## Example Improvements

**Before:**
```
Please help me write tests for this code.
```

**After:**
```
You are an expert test engineer. Analyze the code below and write comprehensive tests.

Requirements:
- Follow AAA pattern (Arrange, Act, Assert)
- Test happy path and edge cases
- Use descriptive test names

<code>
[code here]
</code>

Output your tests with explanations for each test case.
```

**Techniques applied:** Role prompting, XML tags, clear structure, specific requirements

---

## Documentation

See `skills/prompt-engineer/references/` for:
- Complete technique catalog
- Research citations
- Anti-patterns guide
- Stacking strategies

---

## Requirements

**Dependencies:** None

**Compatibility:** Works with any LLM prompt (optimized for Claude)

---

## Version History

See [CHANGELOG.md](./CHANGELOG.md) for detailed version history.

**Latest:** v2.0.0 - Renamed from prompt-optimizer, split reference docs

---

## License

MIT License - See [LICENSE](../../LICENSE)

---

## Author

**Vlad Olaru** - [@vladolaru](https://github.com/vladolaru)

**Repository:** https://github.com/vladolaru/claude-code-plugins

---

## Attribution

Based on research from:
- Anthropic's prompt engineering guide
- Academic research on LLM prompting
- Community best practices

Upstream source: https://github.com/solatis/claude-config

# prompt-engineer

Systematic prompt optimization with evidence-grounded pattern attribution. Every technique recommendation comes with a quoted trigger condition from research — no guessing, no vibes.

Human-in-the-loop approval gates between phases prevent wasted effort. You stay in control throughout.

## How It Works

```bash
/optimize-prompt path/to/prompt.md
```

Five phases, with your approval between each:

1. **Triage** — Is this prompt complex enough to need optimization? Simple prompts stay simple.
2. **Analysis** — Identify issues with quoted evidence from the reference library
3. **Selection** — Choose techniques that match the specific trigger conditions
4. **Optimization** — Generate improved version with pattern attribution
5. **Verification** — Quality check before presenting the result

Works on any prompt — skills (SKILL.md), agent definitions, slash commands, CLAUDE.md files, API system prompts, or freeform prompts for Claude.

## What Makes It Different

Every technique selection is grounded in evidence. Before recommending Chain-of-Thought, XML tags, or any other technique, the optimizer quotes the specific trigger condition from research that justifies applying it here.

The embedded reference library covers 50+ techniques across:

| Category | Techniques |
|----------|-----------|
| **Structural** | Chain-of-Thought, XML tags, few-shot examples, role prompting |
| **Output control** | Format templates, JSON schemas, length control |
| **Quality** | Self-consistency, reasoning steps, anti-pattern detection |
| **Advanced** | Multi-turn strategies, tool use patterns, context management |
| **Compression** | Token efficiency, semantic deduplication |
| **HITL** | Approval gates, feedback loops, human authority preservation |

## Example

**Before:**
```
Please help me write tests for this code.
```

**After:**
```
You are an expert test engineer. Analyze the code below and write
comprehensive tests.

Requirements:
- Follow AAA pattern (Arrange, Act, Assert)
- Test happy path and edge cases
- Use descriptive test names

<code>
[code here]
</code>

Output your tests with explanations for each test case.
```

**Techniques applied:** Role prompting, XML tags, structured requirements, output specification.

## Installation

### Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install prompt-engineer@vladolaru-claude-code-plugins
```

### Codex

```bash
codex plugin marketplace add vladolaru/claude-code-plugins
codex plugin add prompt-engineer@vladolaru-claude-code-plugins
```

Use `/optimize-prompt` in Claude Code or
`$prompt-engineer:optimize-prompt` in Codex. No dependencies. Works with any
LLM prompt and includes host-specific explicit invocation controls.

## Credits

Based on research from Anthropic's prompt engineering guides, academic papers on LLM prompting, and community best practices. Upstream source: [solatis/claude-config](https://github.com/solatis/claude-config).

## License

MIT — see [LICENSE](../../LICENSE).

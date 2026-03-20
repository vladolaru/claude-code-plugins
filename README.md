# vladolaru/claude-code-plugins

My personal Claude Code plugins — open source tools I've built for development workflows, code review, knowledge capture, and prompt optimization.

Each plugin started as something I needed for my own work. They're opinionated, actively maintained, and battle-tested on real projects.

## Quick Start

```bash
# Add the marketplace
/plugin marketplace add vladolaru/claude-code-plugins

# Install what you need
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install dex@vladolaru-claude-code-plugins
/plugin install prompt-engineer@vladolaru-claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
/plugin install yoloing-safe@vladolaru-claude-code-plugins

# Restart Claude Code to activate
```

## Plugins

| Plugin | What it does |
|--------|-------------|
| [**pirategoat-tools**](plugins/pirategoat-tools/README.md) | Development tools — 24 review agents, 19 skills (testing-patterns, software-architecture, WordPress, Figma), rich feedback loops |
| [**dex**](plugins/dex/README.md) | Knowledge capture — frictionless capture of learnings, patterns, and decisions from conversations into agent-first docs |
| [**prompt-engineer**](plugins/prompt-engineer/README.md) | Prompt optimization — evidence-grounded pattern attribution with human-in-the-loop approval gates |
| [**image-optimizer**](plugins/image-optimizer/README.md) | Image optimization — lossless compression for PNG, JPEG, GIF, SVG with review-before-apply workflow |
| [**yoloing-safe**](plugins/yoloing-safe/README.md) | YOLO mode safety net — PreToolUse guardrails that block destructive commands, ask about risky ones, and nudge toward safer alternatives |

### pirategoat-tools

The main plugin. 23 specialized review agents that run in parallel, 19 skills covering testing patterns (190KB reference library), software architecture (87KB pattern library), WordPress/WooCommerce development, Figma-to-code workflows, and browser automation. Agents integrate with linters, coverage tools, and security scanners for ground-truth validation.

**[Full documentation →](plugins/pirategoat-tools/README.md)** | [Changelog](plugins/pirategoat-tools/CHANGELOG.md)

### dex

Frictionless knowledge capture based on [compound engineering](https://every.to/guides/compound-engineering) — each unit of work should make the next one easier, not harder. Fire `/dex` after any engineering work, confirm with a single selection, done. Knowledge lives as agent-first documents in `.claude/docs/` with optional promotion to CLAUDE.md.

**[Full documentation →](plugins/dex/README.md)** | [Changelog](plugins/dex/CHANGELOG.md)

### prompt-engineer

Systematic prompt optimization through a 5-phase workflow with human approval gates between phases. Every technique recommendation is evidence-grounded — quoted trigger conditions from a comprehensive research-backed reference library (50+ techniques).

**[Full documentation →](plugins/prompt-engineer/README.md)** | [Changelog](plugins/prompt-engineer/CHANGELOG.md)

### image-optimizer

Lossless image compression using ImageOptim and svgo. Shows you the before/after size savings, asks for confirmation, then applies. One command: `/optimize-images path/to/images`.

**[Full documentation →](plugins/image-optimizer/README.md)** | [Changelog](plugins/image-optimizer/CHANGELOG.md)

### yoloing-safe

YOLO mode safety net. A `PreToolUse` hook that evaluates every tool call against safety rules — blocks the irreversible stuff (`rm -rf`, `npm publish`, credential access), asks about the risky-but-maybe-intentional stuff (force push, `brew install`, `terraform destroy`), and lets everything else fly. Configurable credential patterns and zero-access paths. Zero dependencies, zero config needed.

**[Full documentation →](plugins/yoloing-safe/README.md)** | [Changelog](plugins/yoloing-safe/CHANGELOG.md)

## Repository Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── plugins/
│   ├── pirategoat-tools/         # 24 agents, 19 skills, 6 commands
│   ├── dex/                      # 7 commands, 1 skill, tests
│   ├── prompt-engineer/          # 1 command, 1 skill, reference library
│   ├── image-optimizer/          # 1 command, optimization scripts
│   └── yoloing-safe/            # PreToolUse safety hook, tests
├── CLAUDE.md
├── LICENSE
└── README.md
```

## Design Patterns

Reusable patterns for building new skills and commands are documented in [`docs/patterns/`](docs/patterns/).

- **[Step-by-step prompt injection](docs/patterns/step-by-step-prompt-injection.md)** — Enforce analytical discipline across multi-phase workflows by driving each step from a Python CLI script. Claude calls the script once per step; the script injects that step's instructions and nothing else. Includes script template, skill file structure, testing checklist, and reference implementations from `decision-critic`.

## Development

```bash
git clone https://github.com/vladolaru/claude-code-plugins.git
cd claude-code-plugins

# Add as local marketplace for testing
/plugin marketplace add /path/to/claude-code-plugins

# Install from local
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
```

## Credits

- **[solatis/claude-config](https://github.com/solatis/claude-config)** — prompt-engineer skill and reference documents
- **[zl190/md-slides](https://github.com/zl190/md-slides)** — creating-md-slides and marp-slide-quality skills
- **[Jim Humelsine's blog](https://jhumelsine.github.io)** — testing-patterns (190KB) and software-architecture (87KB) reference libraries, synthesized from his excellent architecture and testing series
- **[Every, Inc.](https://every.to/guides/compound-engineering)** — compound engineering methodology that inspired dex

## Resources

- [Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Plugins guide](https://docs.anthropic.com/en/docs/claude-code/plugins)
- [Skills guide](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)

## License

MIT — see [LICENSE](LICENSE).

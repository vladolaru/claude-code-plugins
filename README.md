# vladolaru/claude-code-plugins

My personal Claude Code plugins marketplace featuring specialized skills and commands for development workflows.

## Installation

### Add the Marketplace

```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugins

```bash
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
/plugin install prompt-optimizer@vladolaru-claude-code-plugins
```

Then restart Claude Code.

## Plugins

### pirategoat-tools

Vlad Olaru's personal public Claude Code tools - experimental features that may eventually be extracted into standalone plugins.

See [pirategoat-tools CHANGELOG](plugins/pirategoat-tools/CHANGELOG.md) for version history.

#### Skills

| Skill | Description |
|-------|-------------|
| **wordpress-backend-dev** | WordPress plugin/theme PHP development - WPCS coding standards, security patterns, i18n, hooks API, REST API |

#### Commands

| Command | Description |
|---------|-------------|
| `/fix-github-issue <number>` | Analyze and fix a GitHub issue end-to-end |
| `/execute-plan <plan>` | Execute an implementation plan through delegation and quality assurance |

#### Agents

Specialized subagents for the Task tool:

| Agent | Description |
|-------|-------------|
| **architect** | Lead architect - analyzes code, designs solutions, writes ADRs |
| **developer** | Implements specs with tests - delegate for writing code |
| **debugger** | Analyzes bugs through systematic evidence gathering |
| **quality-reviewer** | Reviews code for real issues (security, data loss, performance) |
| **technical-writer** | Creates documentation after feature completion |
| **adr-writer** | Creates ADR documents according to standardized structure |

---

### image-optimizer

Lossless image optimization (PNG, JPEG, GIF, SVG) using imageoptim-cli and svgo with review/confirm workflow.

See [image-optimizer CHANGELOG](plugins/image-optimizer/CHANGELOG.md) for version history.

**Prerequisites:**
```bash
npm install -g imageoptim-cli  # PNG, JPEG, GIF (requires ImageOptim.app on macOS)
npm install -g svgo            # SVG
```

**Usage:** `/optimize-images ./assets`

---

### prompt-optimizer

Two-phase prompt optimization with pattern attribution using proven prompt engineering patterns.

See [prompt-optimizer CHANGELOG](plugins/prompt-optimizer/CHANGELOG.md) for version history.

**Features:**
1. Section-by-section analysis with pattern attribution
2. Full-pass integration for global coherence

Every change is justified with explicit pattern references from the embedded prompt engineering guide.

**Usage:**
- Use the skill: Ask Claude to "optimize this prompt using the prompt-optimizer skill"
- Use the command: `/optimize-prompt`

---

## Skills Detail

### wordpress-backend-dev

Comprehensive WordPress backend development guidance covering:
- PHP Coding Standards (WPCS)
- Security patterns (sanitization, escaping, nonces, capabilities)
- Internationalization (i18n)
- Hooks API (actions & filters)
- Database operations with WPDB
- REST API endpoints and controllers
- AJAX handlers
- Admin menus and settings

**Usage:** Triggered automatically when working on WordPress PHP code or when you encounter PHPCS errors.

## Commands Detail

### /fix-github-issue

Automates the GitHub issue fix workflow:
1. Fetches issue details via `gh issue view`
2. Analyzes the problem
3. Searches codebase for relevant files
4. Implements fixes
5. Runs tests and linting
6. Creates commit and PR

**Usage:**
```
/fix-github-issue 123
/fix-github-issue https://github.com/owner/repo/issues/123
```

### /execute-plan

Project manager mode for executing implementation plans:
- Tracks progress with TodoWrite
- Delegates implementation to specialized agents
- Validates each increment
- Enforces quality gates

**Usage:**
```
/execute-plan Read the plan from ./docs/implementation-plan.md and execute it
```

## Repository Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── plugins/
│   ├── pirategoat-tools/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── agents/               # Subagent definitions
│   │   ├── commands/             # Slash commands
│   │   └── skills/               # Skills with SKILL.md files
│   ├── image-optimizer/
│   │   ├── CHANGELOG.md          # Version history
│   │   └── skills/               # Skills with SKILL.md files
│   └── prompt-optimizer/
│       ├── CHANGELOG.md          # Version history
│       ├── commands/             # Slash commands
│       └── skills/               # Skills with SKILL.md files
├── CLAUDE.md                     # Development instructions
├── LICENSE
└── README.md
```

## Development

To test changes locally:

```bash
# Clone the repo
git clone https://github.com/vladolaru/claude-code-plugins.git
cd claude-code-plugins

# Add as local marketplace
/plugin marketplace add /path/to/claude-code-plugins

# Install from local
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
/plugin install prompt-optimizer@vladolaru-claude-code-plugins
```

## Credits

The following components were adapted from [solatis/claude-config](https://github.com/solatis/claude-config):
- **prompt-optimizer** skill
- All **agents** (architect, developer, debugger, quality-reviewer, technical-writer, adr-writer)

## Resources

- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code/overview)
- [Plugins Guide](https://docs.claude.com/en/docs/claude-code/plugins)
- [Skills Guide](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
- [Slash Commands Reference](https://docs.claude.com/en/docs/claude-code/slash-commands)

## License

MIT License - see [LICENSE](LICENSE) file for details.

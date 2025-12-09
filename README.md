# vladolaru/claude-code-plugins

My personal Claude Code plugins featuring specialized skills and commands for development workflows.

## Installation

### Add the Marketplace

```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install the Plugin

```bash
/plugin install vlad-tools@vladolaru-plugins
```

Then restart Claude Code.

## Contents

### Skills

| Skill | Description |
|-------|-------------|
| **image-optimizer** | Lossless image optimization (PNG, JPEG, GIF, SVG) using imageoptim-cli and svgo with review/confirm workflow |
| **prompt-optimizer** | Optimize Claude Code agent prompts using proven prompt engineering patterns with explicit attribution |
| **wordpress-backend-dev** | WordPress plugin/theme PHP development - WPCS coding standards, security patterns, i18n, hooks API, REST API |

### Commands

| Command | Description |
|---------|-------------|
| `/fix-github-issue <number>` | Analyze and fix a GitHub issue end-to-end |
| `/execute-plan <plan>` | Execute an implementation plan through delegation and quality assurance |

### Agents

Specialized subagents for the Task tool:

| Agent | Description |
|-------|-------------|
| **architect** | Lead architect - analyzes code, designs solutions, writes ADRs |
| **developer** | Implements specs with tests - delegate for writing code |
| **debugger** | Analyzes bugs through systematic evidence gathering |
| **quality-reviewer** | Reviews code for real issues (security, data loss, performance) |
| **technical-writer** | Creates documentation after feature completion |
| **adr-writer** | Creates ADR documents according to standardized structure |

## Skills Detail

### image-optimizer

Losslessly reduces image file sizes with a safe review-before-apply workflow.

**Prerequisites:**
```bash
npm install -g imageoptim-cli  # PNG, JPEG, GIF (requires ImageOptim.app on macOS)
npm install -g svgo            # SVG
```

**Usage:** Ask Claude to "optimize images in ./assets" and follow the interactive workflow.

### prompt-optimizer

Two-phase prompt optimization:
1. Section-by-section analysis with pattern attribution
2. Full-pass integration for global coherence

Every change is justified with explicit pattern references from the embedded prompt engineering guide.

**Usage:** Provide a prompt and ask Claude to "optimize this prompt using the prompt-optimizer skill."

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

## Development

To test changes locally:

```bash
# Clone the repo
git clone https://github.com/vladolaru/claude-code-plugins.git
cd claude-code-plugins

# Add as local marketplace
/plugin marketplace add /path/to/claude-code-plugins

# Install from local
/plugin install vlad-tools@vladolaru-plugins
```

## Resources

- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code/overview)
- [Plugins Guide](https://docs.claude.com/en/docs/claude-code/plugins)
- [Skills Guide](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
- [Slash Commands Reference](https://docs.claude.com/en/docs/claude-code/slash-commands)

## License

MIT License - see [LICENSE](LICENSE) file for details.
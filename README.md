# vladolaru/claude-code-plugins

My personal Claude Code plugins marketplace featuring specialized skills, commands, and agents for development workflows.

## Installation

### Add the Marketplace

```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugins

```bash
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
/plugin install prompt-engineer@vladolaru-claude-code-plugins
```

Then restart Claude Code.

## Plugins

### pirategoat-tools (v1.5.0)

Vlad Olaru's personal public Claude Code tools - experimental features that may eventually be extracted into standalone plugins.

See [pirategoat-tools CHANGELOG](plugins/pirategoat-tools/CHANGELOG.md) for version history.

#### Skills

| Skill | Description |
|-------|-------------|
| **wordpress-backend-dev** | WordPress plugin/theme PHP development - WPCS coding standards, security patterns, i18n, hooks API, REST API |
| **browser-interaction** | Browser automation for debugging, verification, testing using MCP servers (chrome-devtools, playwright, puppeteer) |
| **woocommerce-browser-interaction** | WooCommerce-specific browser automation patterns (login, admin, frontend, block checkout) |
| **dig-into-linear-issue** | Thorough Linear issue investigation workflow with RCA templates and validation paths |
| **pr-reviewing** | Structured PR review workflow ensuring context gathering before code review |
| **creating-md-slides** | Create presentation slides from Markdown using Marp (PDF, PPTX, HTML output) |
| **marp-slide-quality** | Analyze and improve Marp presentations using SlideGauge quality checks |

#### Commands

| Command | Description |
|---------|-------------|
| `/fix-github-issue <number>` | Analyze and fix a GitHub issue end-to-end |
| `/execute-plan <plan>` | Execute an implementation plan through delegation and quality assurance |

#### Agents

Specialized review agents for the Task tool:

| Agent | Description |
|-------|-------------|
| **pr-reviewer** | Generalist PR reviewer - validates code changes against stated goals |
| **security-reviewer** | WordPress security review (XSS, SQL injection, CSRF/nonces, capabilities, sanitization/escaping) |
| **performance-reviewer** | WordPress performance review (N+1 queries, caching/transients, autoloaded options, WP_Query) |
| **architecture-reviewer** | WordPress architecture review (hooks/extensibility, WPCS, backwards compatibility, i18n) |
| **patterns-reviewer** | Explores codebase and git history for existing patterns, ensures consistency |
| **gemini-reviewer** | Cross-validates PR changes using Google Gemini CLI |
| **codex-reviewer** | Cross-validates PR changes using OpenAI Codex CLI |
| **review-reconciliator** | Reads all review files, reconciles findings, produces consolidated summary |
| **technical-writer** | Creates documentation after feature completion |

---

### image-optimizer (v1.1.0)

Lossless image optimization with review/confirm workflow.

- **Raster (PNG, JPEG, GIF):** Fully lossless optimization using ImageOptim - reduces file size without any quality loss
- **SVG:** Uses [svgo](https://github.com/svg/svgo), the same optimizer powering [SVGOMG](https://svgomg.net/), with web-safe default techniques

See [image-optimizer CHANGELOG](plugins/image-optimizer/CHANGELOG.md) for version history.

**Prerequisites:**
```bash
npm install -g imageoptim-cli  # PNG, JPEG, GIF (requires ImageOptim.app on macOS)
npm install -g svgo            # SVG
```

**Usage:** `/optimize-images ./assets`

---

### prompt-engineer (v2.0.0)

Human-in-the-loop prompt optimization with evidence-grounded pattern attribution using proven prompt engineering patterns.

See [prompt-engineer CHANGELOG](plugins/prompt-engineer/CHANGELOG.md) for version history.

**Features:**
- 5-phase workflow with user approval gates between phases
- Quote-first evidence grounding - all technique selections require quoted triggers from reference
- Phase 0 triage to avoid over-engineering simple prompts
- Split reference documents for single-turn and multi-turn/multi-agent flows

**Usage:**
- Use the skill: Ask Claude to "optimize this prompt using the prompt-engineer skill"
- Use the command: `/optimize-prompt`

---

## Repository Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── plugins/
│   ├── pirategoat-tools/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── agents/               # Review agents (9 agents)
│   │   ├── commands/             # Slash commands
│   │   └── skills/               # Skills (7 skills)
│   ├── image-optimizer/
│   │   ├── CHANGELOG.md          # Version history
│   │   ├── commands/             # Slash commands
│   │   └── scripts/              # Optimization scripts
│   └── prompt-engineer/
│       ├── CHANGELOG.md          # Version history
│       ├── commands/             # Slash commands
│       └── skills/               # Skills with reference docs
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
/plugin install prompt-engineer@vladolaru-claude-code-plugins
```

## Credits

The following components were adapted from [solatis/claude-config](https://github.com/solatis/claude-config):
- **prompt-engineer** skill and reference documents

The following components were adapted from [zl190/md-slides](https://github.com/zl190/md-slides):
- **creating-md-slides** skill
- **marp-slide-quality** skill

## Resources

- [Claude Code Documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Plugins Guide](https://docs.anthropic.com/en/docs/claude-code/plugins)
- [Skills Guide](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)
- [Slash Commands Reference](https://docs.anthropic.com/en/docs/claude-code/slash-commands)

## License

MIT License - see [LICENSE](LICENSE) file for details.

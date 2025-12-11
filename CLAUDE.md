# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is **vladolaru-claude-code-plugins** - Vlad Olaru's personal Claude Code plugin marketplace featuring specialized plugins for development workflows, WordPress backend development, and AI-powered tools.

## Architecture

### Plugin Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── plugins/
│   └── plugin-name/
│       ├── CHANGELOG.md          # Version history
│       ├── agents/               # Subagent definitions (optional)
│       │   └── agent-name.md
│       ├── commands/             # Slash commands (optional)
│       │   └── command.md
│       ├── skills/               # Skills with SKILL.md files (optional)
│       │   └── skill-name/
│       │       └── SKILL.md
│       └── scripts/              # Helper scripts (optional)
├── CLAUDE.md                     # This file
├── LICENSE
└── README.md
```

### Skills Specification

All skills must have a `SKILL.md` file with YAML frontmatter:

- **Required frontmatter fields**:
  - `name` - hyphen-case, lowercase alphanumeric + hyphens
  - `description` - when Claude should use this skill
- **Optional frontmatter fields**:
  - `license`
  - `metadata` - custom key-value pairs
- **Body**: Markdown instructions, examples, and guidelines

## Using This Marketplace

### Adding to Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Installing Plugins

```bash
# Browse available plugins
/plugin

# Install specific plugin
/plugin install pirategoat-tools@vladolaru-claude-code-plugins
```

## Creating New Plugins

1. **Create plugin directory** under `plugins/`:

   ```bash
   mkdir -p plugins/my-plugin/{skills,commands,agents}
   ```

2. **Add CHANGELOG.md**:

   ```markdown
   # Changelog

   ## [1.0.0] - YYYY-MM-DD

   ### Added
   - Initial release
   ```

3. **Create skills/commands/agents** as needed

4. **Register in marketplace.json**:

   ```json
   {
     "name": "my-plugin",
     "source": "./plugins/my-plugin",
     "description": "Plugin description",
     "version": "1.0.0",
     "author": { "name": "Vlad Olaru" },
     "repository": "https://github.com/vladolaru/claude-code-plugins",
     "license": "MIT",
     "keywords": ["keyword1", "keyword2"],
     "category": "development-tools",
     "strict": true,
     "skills": ["./skills/my-skill"],
     "commands": ["./commands/my-command.md"],
     "agents": ["./agents/my-agent.md"]
   }
   ```

## Versioning & Releases

### Plugin-Prefixed Tags

Since this repository may contain multiple plugins with independent version cycles, use **plugin-prefixed tags**:

**Tag Format:** `<plugin-name>/v<semver>`

**Examples:**
- `pirategoat-tools/v1.0.0`
- `pirategoat-tools/v1.1.0`

### Release Process

Follow these steps to release a new plugin version:

**1. Update CHANGELOG.md**

Add a new version section following [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Fixed
- Bug fixes

### Changed
- Changes to existing functionality
```

**2. Update marketplace.json**

Update the plugin's version in `.claude-plugin/marketplace.json`:

```json
{
  "name": "plugin-name",
  "version": "X.Y.Z",
  ...
}
```

**3. Commit Changes**

Use conventional commit format with version in message:

```bash
# Commit with semantic type prefix
git add -A
git commit -m "fix(plugin-name): description of changes"
# or
git commit -m "feat(plugin-name): description of changes"

# Push to repository
git push
```

**Commit Types:**
- `fix:` - Bug fixes (patch version bump)
- `feat:` - New features (minor version bump)
- `BREAKING CHANGE:` - Breaking changes (major version bump)
- `docs:`, `chore:`, `refactor:` - Other changes

**4. Create Plugin-Prefixed Tag**

```bash
# Create and push tag
git tag <plugin-name>/vX.Y.Z
git push --tags
```

**5. Create GitHub Release (Optional)**

```bash
gh release create <plugin-name>/vX.Y.Z \
  --title "<plugin-name> vX.Y.Z" \
  --notes "## Changes

- Description of changes"
```

Copy the relevant sections from CHANGELOG.md for the release notes.

### Why Plugin-Prefixed Tags?

1. **Clarity** - Unambiguous which plugin a tag refers to
2. **Independence** - Each plugin can have its own release cycle
3. **Scalability** - Add more plugins without version conflicts
4. **Standard Practice** - Common pattern for monorepos

## License

MIT License - See LICENSE file for details.

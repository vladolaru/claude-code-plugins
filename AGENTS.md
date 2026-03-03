# AGENTS.md

This file provides shared project guidance for agent runtimes working with this repository.

## Repository Overview

This is **vladolaru-claude-code-plugins** - Vlad Olaru's personal Claude Code plugin marketplace featuring specialized plugins for development workflows, WordPress backend development, and AI-powered tools.

**Development model:** This project is AI-written and AI-maintained with human guidance and decisions. The human (Vlad) sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance. "Single maintainer" does not mean capacity-constrained — it means single human decision-maker with AI execution capacity. Do not assume limited implementation bandwidth when reasoning about priorities or feasibility.

## Architecture

### Plugin Structure

```text
vladolaru-claude-code-plugins/
├── .claude-plugin/
│   └── marketplace.json          # Plugin registry
├── CLAUDE.md                     # Claude Code shim -> @AGENTS.md
├── AGENTS.md                     # Canonical shared instructions
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

## Testing

### pirategoat-tools

The `plugins/pirategoat-tools/tests/` directory contains deterministic evals (no model calls) for plugin scripts. See `tests/TESTING.md` for the full framework documentation: architecture, design principles, how to add tests/graders/scenarios, and conventions.

**When to run tests:** After modifying any of these files, run the relevant test suite:

| Changed file | Run |
|---|---|
| `scripts/bootstrap-reviewer.py` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `agents/shared/reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `agents/shared/tests-reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` |
| `scripts/review_output_simple.py` | `pytest plugins/pirategoat-tools/tests/test_graders.py -v` |
| `tests/graders.py` | `pytest plugins/pirategoat-tools/tests/test_graders.py -v` |
| Any reviewer agent `.md` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` (verifies agent config still works) |
| New agent added to `AGENT_CONFIG` | `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v` (auto-included in all parameterized tests) |
| Any review command `.md` | `pytest plugins/pirategoat-tools/tests/test_commands.py -v` (validates structure, agent refs, script refs) |
| `.claude-plugin/marketplace.json` | `pytest plugins/pirategoat-tools/tests/test_commands.py -v` (validates command registration, agent cross-refs) |

**Run all tests:** `pytest plugins/pirategoat-tools/tests/ -v`

**Test principles:**
- Code-based graders only (fast, deterministic, no model calls)
- Grade outcomes not paths
- Test both positive and negative cases
- Integration tests run `bootstrap-reviewer.py` via subprocess against all 11 agents

**Agent compliance eval** (requires `claude` CLI, makes model calls):
```bash
# Grade existing review output files
python3 plugins/pirategoat-tools/tests/eval_agent_compliance.py --grade-only /tmp/pr-review-<N>

# Full dispatch eval (slow, model calls)
python3 plugins/pirategoat-tools/tests/eval_agent_compliance.py --dispatch --agent security-reviewer
```

## Versioning & Releases

### RULE 0: Every Change Gets Documented

Every commit that modifies plugin behavior (features, fixes, refactors, performance) **must** include:

1. **CHANGELOG.md update** — Add an entry under the appropriate version section in the plugin's `CHANGELOG.md`
2. **Version bump in marketplace.json** — Update the plugin's `version` field following semver (`feat` = minor, `fix`/`refactor`/`perf` = patch, `BREAKING CHANGE` = major)

**Coalescing rule:** If the latest version bump has not been pushed to the remote yet, fold new changes into the same version entry rather than bumping again — provided they are of similar impact (e.g., two fixes, or a feature and a closely related fix). If the new change is a higher semver impact (e.g., existing unpushed patch + new feature), upgrade the version to match.

**Exempt from version bumps:** `docs`, `test`, `ci`, `style`, `chore` commits that don't change runtime behavior. Still add a changelog entry if the change is notable.

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

## AI Artifacts

All AI-generated artifacts (plans, analysis, research, decisions, learnings) go under `.claude/docs/`:

```text
.claude/docs/
├── analysis/     # Research findings, investigations, session analysis
├── decisions/    # Architecture Decision Records
├── learnings/    # Debugging insights, gotchas, fixes
├── patterns/     # Reusable workflows, conventions, anti-patterns
├── plans/        # Implementation plans
└── research/     # Deep-dive research (e.g., a11y/, figma/)
```

**RULE:** Never create AI artifacts under `docs/` at the repo root or under `plugins/*/docs/`. Those locations are for committed documentation that ships with the plugin (guides, READMEs). Working artifacts from agent sessions go in `.claude/docs/`.

## Design Patterns

Established patterns live in `docs/patterns/`. Consult them before implementing similar functionality.

| Pattern | When to use |
|---|---|
| [step-by-step-prompt-injection](docs/patterns/step-by-step-prompt-injection.md) | Multi-phase analytical workflows where later steps must be independent of earlier conclusions — e.g., verify before judge, gather before synthesize. Includes script template, skill file structure, testing checklist, and two reference implementations. |

## Knowledge Capture

- After significant debugging sessions, architectural decisions, or discovering non-obvious behavior, suggest using `/dex:grok` to capture the knowledge.

## License

MIT License - See LICENSE file for details.

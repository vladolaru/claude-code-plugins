# Codex Interop

`codex_interop` is a generator and reconcile layer that projects the Claude Code plugin ecosystem installed on a local machine into Codex-compatible artifacts.

It exists to avoid maintaining two parallel systems:

- Claude Code remains the canonical runtime and authoring model
- Codex gets generated compatibility artifacts

That is the central design decision:

- Claude-side skills and agents are the only canonical authored source
- Codex-side skills, prompts, and config are generated projections
- this directory should never become a second hand-maintained skill or agent ecosystem

The canonical authored sources stay outside this directory:

- root [`AGENTS.md`](/Users/vladolaru/Work/a8c/claude-code-plugins/AGENTS.md)
- `plugins/*/skills/*/SKILL.md`
- `plugins/*/agents/*.md`
- plugin-local `scripts/`, `references/`, `assets/`, and related resources

## What It Generates

Project-local outputs:

- `CLAUDE.md`
  - generated only as the shim `@AGENTS.md`
- `.codex/config.toml`
  - inline Codex multi-agent config
- `.codex/prompts/agents/*.md`
  - generated prompt payloads for translated Claude agents
- `.codex/interop-state.json`
  - generator ownership/state tracking

User-global outputs:

- `~/.codex/skills/<plugin>-<skill>/`
  - generated Codex skills derived from installed Claude skills

Important output boundary:

- `.codex/config.toml` is intentionally local-only and should not be committed
- Codex skills are intentionally materialized only under `~/.codex/skills/`
- there is no project-local Codex skill mirror

## Core Constraints

- `AGENTS.md` is canonical and hand-authored.
- `AGENTS.md` is strictly shared-only.
- The generator never modifies `AGENTS.md`.
- `AGENTS.md` must not contain Codex-only or Claude-only runtime wiring.
- Claude Code behavior must remain unchanged.
- The only Claude-facing generated artifact is `CLAUDE.md`, and only as `@AGENTS.md`.
- Inputs come from actually installed Claude Code plugins under `~/.claude/plugins/cache/...`.
- Project discovery is only `cwd` or `--project`.
- There is no filesystem-wide project crawling.
- If an installed plugin path resolves through a symlink into a repo checkout, the generator follows the resolved repo path.
- If multiple installed versions exist, the latest version is selected by semver.

## What It Does

1. Resolves the target project from `cwd` or `--project`.
2. Requires that the project root contains `AGENTS.md`.
3. Discovers installed Claude plugins from the local Claude cache.
4. Selects the latest installed version of each plugin by semver.
5. Translates Claude agents into:
   - `.codex/prompts/agents/*.md`
   - `.codex/config.toml` `[agents.<role>]` entries
6. Translates Claude skills into self-contained Codex skills under `~/.codex/skills/`.
7. Reconciles those generated artifacts safely and idempotently.

The intended split is:

- shared project guidance stays in `AGENTS.md`
- Claude-native behavior continues to come from Claude’s own plugin/runtime model
- Codex-specific runtime behavior lives only in generated `.codex/*` and `~/.codex/skills/*`

## Ownership and Safety

Never touched:

- `AGENTS.md`
- `plugins/**`
- hand-authored project files

Generator-owned:

- `CLAUDE.md` when it is the exact `@AGENTS.md` shim
- `.codex/config.toml`
- `.codex/prompts/agents/*`
- `.codex/interop-state.json`
- generated Codex skills in `~/.codex/skills/*`

Safety rules:

- refuses to overwrite a hand-authored `CLAUDE.md`
- refuses to overwrite non-generated `.codex` files
- refuses to overwrite non-generated Codex skill directories
- uses staged writes with rollback-safe backup/rename replacement
- keeps state deterministic so rerunning without input changes is a no-op

In practice, existing hand-authored files are treated as authoritative unless they are explicitly generator-owned artifacts.

## Commands

Run from the repo root or pass `--project`.

Validate only:

```bash
python3 codex_interop/cli.py validate --project .
```

Preview changes without writing:

```bash
python3 codex_interop/cli.py dry-run --project .
```

Show file-level diffs:

```bash
python3 codex_interop/cli.py diff --project .
```

Apply generated outputs:

```bash
python3 codex_interop/cli.py reconcile --project .
```

Useful overrides:

- `--cache-dir /path/to/claude/plugins/cache`
- `--codex-home /path/to/codex/home`

These are mainly useful for testing or controlled local runs.

Project selection is intentionally narrow:

- default target = current working directory
- override target = `--project /path/to/project`
- valid project = directory containing `AGENTS.md`
- no machine-wide repo discovery

## macOS Scheduling

The supported automation path is `launchd`, not a long-lived watcher.

That is also a deliberate decision:

- scheduled reconcile is the supported macOS automation mode
- watcher mode is deferred
- installed Claude plugin state remains the source of truth

Print a LaunchAgent plist:

```bash
python3 codex_interop/cli.py print-launchagent --project .
```

Install a LaunchAgent plist into `~/Library/LaunchAgents/`:

```bash
python3 codex_interop/cli.py install-launchagent --project .
```

Optional scheduling overrides:

- `--interval 900`
- `--cache-dir ...`
- `--codex-home ...`
- `--logs-dir ...`
- `--python-executable ...`
- `--cli-path ...`
- `--label ...`

`install-launchagent` writes the plist and prints the next `launchctl bootstrap` command. It does not run `launchctl` automatically.

## Implementation Notes

Agent translation:

- Claude agent markdown frontmatter is parsed and mapped into Codex role config
- original Claude model hints are preserved as metadata/comments
- Claude tool names are translated to Codex tool identifiers where possible

Skill translation:

- official skill layout is copied: `SKILL.md`, `scripts/`, `references/`, `assets/`, `agents/`
- generated skill `name:` matches the generated parent directory name
- plugin-root path references and sibling-skill references are rewritten when relocation requires it
- non-skill junk such as `.venv/`, `__pycache__/`, `.pyc`, `.DS_Store` is excluded

## Tests

Run the test suite:

```bash
pytest codex_interop/tests -q
```

Run coverage:

```bash
pytest --cov=codex_interop --cov-report=term-missing codex_interop/tests -q
```

Current status at the time this README was added:

- `54` tests passing
- `96%` total coverage

## Scope Boundary

This implementation covers:

- installed-plugin discovery
- agent translation
- skill translation
- reconcile and state tracking
- macOS LaunchAgent generation/installation

It intentionally does not cover:

- a filesystem watcher mode
- a `clean` command
- changing Claude Code’s native runtime model

This README is the practical summary. The full decision record remains in the spec and implementation plan linked below.

For deeper design context, see:

- [generator spec](/Users/vladolaru/Work/a8c/claude-code-plugins/.claude/docs/plans/2026-03-03-codex-interop-generator-spec.md)
- [implementation plan](/Users/vladolaru/Work/a8c/claude-code-plugins/.claude/docs/plans/2026-03-03-codex-interop-generator-implementation-plan.md)

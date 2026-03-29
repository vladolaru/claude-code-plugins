# Plugin Environment Variables Reference

Reference guide for Claude Code plugin environment variables — what they resolve to, where they work, and known limitations. Based on source code analysis of Claude Code v2.1.87.

---

## 1. Path Variables (String Substitutions)

These are substituted inline in plugin content and/or exported to subprocesses. They are the primary mechanism for plugins to reference their own files.

### Substitution Matrix

The source has two distinct code paths — one for plugin content and one for non-plugin content (`.claude/skills/`, `.claude/commands/`). Within the plugin path, commands and skills differ only in `CLAUDE_SKILL_DIR`:

| Variable | Plugin Commands | Plugin Skills | Non-plugin skills/commands | Hooks |
|----------|:-:|:-:|:-:|:-:|
| `${CLAUDE_PLUGIN_ROOT}` | Yes | Yes | — | Yes (inline + env) |
| `${CLAUDE_SKILL_DIR}` | — | Yes | Yes (when skillRoot set) | — |
| `${CLAUDE_PLUGIN_DATA}` | Yes | Yes | — | Yes (inline + env) |
| `${CLAUDE_SESSION_ID}` | Yes | Yes | Yes | — |
| `$ARGUMENTS` / `$N` | Yes | Yes | Yes | — |

### Substitution Order (plugin skills)

Substitutions are applied in this order:

1. Base directory prepend (skills only)
2. Argument substitution (`$ARGUMENTS`, `$1`, `$2`, named args)
3. Plugin path substitution (`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`)
4. User config substitution (`${user_config.*}`)
5. Skill dir substitution (`${CLAUDE_SKILL_DIR}`) — skills only
6. Session ID (`${CLAUDE_SESSION_ID}`)
7. `@` reference resolution

---

### `${CLAUDE_PLUGIN_ROOT}`

**Added:** Pre-v2.0 (present since the plugin system launched).

The absolute path to the plugin's installation directory.

```
plugins/pirategoat-tools/          <-- CLAUDE_PLUGIN_ROOT points here
├── scripts/
│   └── review/pipeline.py
├── skills/
│   └── bar/SKILL.md
└── hooks/hooks.json
```

**Where it works:**

| Context | Mechanism | Reliable? |
|---------|-----------|-----------|
| Plugin command content (`commands/*.md`) | Substituted inline | Yes |
| Plugin skill content (`SKILL.md` body) | Substituted inline | Yes |
| `hooks.json` commands | Substituted inline + exported as env var | Yes |
| MCP server configs | Substituted inline | Yes |
| LSP server configs | Substituted inline | Yes |

**Not available in:** Non-plugin skills/commands (`.claude/skills/`, `.claude/commands/`) — there is no plugin context to resolve.

**Hook subtlety:** Hooks registered by skills get `CLAUDE_PLUGIN_ROOT` set to the skill's root directory, not the plugin root.

**Known issues:**

| Issue | Status |
|-------|--------|
| SessionStart hooks — env var not always populated | [#24529](https://github.com/anthropics/claude-code/issues/24529) (OPEN) |
| After plugin updates, could point to stale cached path | [#15642](https://github.com/anthropics/claude-code/issues/15642) (CLOSED) |

**Caveat:** The path changes when the plugin updates. Files written here do not survive updates — use `CLAUDE_PLUGIN_DATA` for persistent state.

### Path Resolution: Two Code Paths

The plugin loader has two resolution strategies for `CLAUDE_PLUGIN_ROOT`, selected per plugin at session startup:

**Cached path (normal):**
Uses the versioned cache directory at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. This is a snapshot extracted from the marketplace git repo at a specific version tag.

**Marketplace fallback:**
Reads from the marketplace git clone at `~/.claude/plugins/marketplaces/<marketplace>/plugins/<plugin>/`. Tries to copy to the versioned cache first. If the copy fails (e.g., concurrent sessions racing to write the same cache entry), falls back to the marketplace path directly.

**What this means in practice:**

`CLAUDE_PLUGIN_ROOT` can resolve to **either** path for the same plugin, depending on timing:

```
# Normal (versioned, immutable snapshot):
~/.claude/plugins/cache/vladolaru-claude-code-plugins/pirategoat-tools/1.93.1/

# Fallback (live git clone, mutable):
~/.claude/plugins/marketplaces/vladolaru-claude-code-plugins/plugins/pirategoat-tools/
```

The marketplace fallback is a **live git clone at HEAD** — its content can change between sessions or mid-session if a background marketplace refresh runs. Two parallel sessions can see different plugin content for the same plugin version.

**Observed behavior (v2.1.87, March 29 2026):** Two sessions started within 30 minutes of each other, same CC version, same plugin version. Session A got `cache/.../1.93.1/`, Session B got `marketplaces/.../plugins/pirategoat-tools/`. The `installed_plugins.json` metadata file was stale (pointing to v1.13.1 from February), but the cache directory had versions up to 1.93.3.

**Implications for plugin authors:**
- Do not assume `CLAUDE_PLUGIN_ROOT` is an immutable snapshot — it might be a live git clone.
- Do not assume the path structure — `cache/` has a flat plugin directory, `marketplaces/` has the full repo structure with `plugins/` prefix.
- Scripts should be resilient to either path. Since both paths contain the same file tree rooted at the plugin directory, relative paths within the plugin work consistently.

---

### `${CLAUDE_SKILL_DIR}`

**Added:** v2.1.69 (March 5, 2026).

The directory containing the skill's `SKILL.md` file. For plugin skills, this is the skill's subdirectory, **not** the plugin root.

```
plugins/pirategoat-tools/
├── skills/
│   └── bar/
│       ├── SKILL.md              <-- CLAUDE_SKILL_DIR points here (the bar/ dir)
│       └── helpers/
│           └── validate.py
```

**Where it works:**

| Context | Mechanism | Reliable? |
|---------|-----------|-----------|
| Plugin skill content (`SKILL.md` body) | Substituted inline | Yes |
| Non-plugin skill content (`.claude/skills/`) | Substituted inline | Yes |

**Where it does NOT work:** Plugin commands (`commands/*.md`) — the `isSkillMode` flag is false, so `CLAUDE_SKILL_DIR` substitution is skipped.

**Not a replacement for `CLAUDE_PLUGIN_ROOT`.** They resolve to different directories:

```
${CLAUDE_PLUGIN_ROOT}  →  /path/to/pirategoat-tools/
${CLAUDE_SKILL_DIR}    →  /path/to/pirategoat-tools/skills/bar/
```

Use `CLAUDE_SKILL_DIR` when a skill needs to reference files alongside its own `SKILL.md`. Use `CLAUDE_PLUGIN_ROOT` when you need the plugin root (e.g., shared scripts in `scripts/`).

---

### `${CLAUDE_PLUGIN_DATA}`

**Added:** v2.1.78 (March 17, 2026).

A persistent directory for plugin state that survives plugin updates. Resolves to `~/.claude/plugins/data/{source-sanitized}/` where `source-sanitized` is the plugin source string with non-alphanumeric characters replaced by hyphens.

**Where it works:** Same contexts as `CLAUDE_PLUGIN_ROOT` — plugin commands, plugin skills, hooks, MCP/LSP configs.

**Use cases:** Installed dependencies (node_modules, Python venvs), generated caches, any files that should persist across plugin versions.

**Lifecycle:** Directory created automatically via `mkdirSync(recursive: true)` on first access. Deleted when the plugin is uninstalled from the last scope (unless `--keep-data` is passed).

---

## 2. Other String Substitutions

| Variable | Plugin commands | Plugin skills | Non-plugin | Purpose |
|----------|:-:|:-:|:-:|---|
| `$ARGUMENTS` | Yes | Yes | Yes | All arguments passed when invoking |
| `$ARGUMENTS[N]` / `$N` | Yes | Yes | Yes | Specific argument by 0-based index |
| `${CLAUDE_SESSION_ID}` | Yes | Yes | Yes | Current session ID |
| `${user_config.*}` | Yes | Yes | — | User config value substitution |

---

## 3. Operational Environment Variables

These control plugin system behavior, not content substitution:

| Variable | Purpose | Added |
|----------|---------|-------|
| `CLAUDE_CODE_PLUGIN_SEED_DIR` | Read-only plugin seed directory paths for container deployments (`:` separated) | v2.1.79 (Mar 18, 2026) |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` | Override the plugin cache directory location | Pre-v2.1.72 |
| `CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS` | Timeout for git operations during plugin install/update (default: 120000ms) | v2.1.51 (Feb 24, 2026) |
| `FORCE_AUTOUPDATE_PLUGINS` | Force plugin auto-updates even when main auto-updater is disabled | Documented |

---

## 4. Decision Guide

| I need to... | Use |
|--------------|-----|
| Reference a shared script from a plugin skill | `${CLAUDE_PLUGIN_ROOT}/scripts/...` |
| Reference a shared script from a plugin command | `${CLAUDE_PLUGIN_ROOT}/scripts/...` |
| Reference a file next to my `SKILL.md` | `${CLAUDE_SKILL_DIR}/...` |
| Reference a script from `hooks.json` | `${CLAUDE_PLUGIN_ROOT}/scripts/...` |
| Store persistent state (caches, deps) | `${CLAUDE_PLUGIN_DATA}/...` |

---

## 5. Changelog Timeline

| Version | Date | Change |
|---------|------|--------|
| v2.1.51 | Feb 24 | Added `CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS` (default 120s) |
| v2.1.69 | Mar 5 | Added `${CLAUDE_SKILL_DIR}` for skills to reference own directory |
| v2.1.72 | Mar 10 | Fixed hooks silently dropped when two plugins use same template; fixed `CLAUDE_CODE_PLUGIN_CACHE_DIR` creating literal `~` directories |
| v2.1.74 | Mar 12 | Fixed `/plugin install` failing in REPL for marketplace plugins |
| v2.1.77 | Mar 17 | Updated `--plugin-dir` to accept one path per flag |
| v2.1.78 | Mar 17 | Added `${CLAUDE_PLUGIN_DATA}` for persistent plugin state |
| v2.1.79 | Mar 18 | Added `CLAUDE_CODE_PLUGIN_SEED_DIR` with multiple-directory support |

---

## 6. Source Code References

Analysis based on Claude Code v2.1.87 compiled binary. Key subsystems identified through string extraction and control flow analysis:

| Subsystem | Role |
|-----------|------|
| Variable substitution | Core `${...}` replacement — handles all plugin/skill/session variables in content strings |
| Plugin content loader | Loads plugin commands and skills, applies variable substitution |
| Non-plugin content loader | Loads `.claude/skills/` and `.claude/commands/` — subset of substitutions (no `PLUGIN_ROOT`/`PLUGIN_DATA`) |
| Cached plugin resolver | Uses versioned `installPath` from `installed_plugins.json` or cache directory |
| Marketplace plugin resolver | Resolves from marketplace git clone, copies to versioned cache, falls back to marketplace path on failure |
| Plugin component resolver | Reads `plugin.json` manifest, resolves commands/agents/skills/hooks/output-styles paths |
| MCP/extension config substitution | Generic string/object substitution for `__dirname`, `user_config.*`, and system directories |

Full source analysis: `.claude/docs/analysis/2026-03-29-plugin-env-vars-source-analysis.md`

# Codex CLI Reference

Reference guide for integrating the OpenAI Codex CLI into automation pipelines. Based on source code analysis of `github.com/openai/codex` and runtime testing against v0.116.0. Sections 2, 6, and 7 updated from GitHub release notes through v0.117.0 (March 26, 2026).

## Architecture Overview

The npm package (`@openai/codex`) is a thin Node.js wrapper that detects the platform, locates a platform-specific native binary (Rust), and spawns it with `process.argv`. All logic lives in the Rust binary.

The Rust codebase is organized as a workspace:
- `codex-rs/cli/` — The interactive TUI (`codex` with no subcommand)
- `codex-rs/exec/` — The non-interactive `codex exec` binary
- `codex-rs/core/` — Shared library: config, session, instructions, skills, review, tools, MCP, sandbox
- `codex-rs/protocol/` — Shared protocol types

Both `codex exec` and the interactive `codex` CLI are separate binaries that share `codex-core`.

---

## 1. Prompting Contracts

### Inline Prompt

```bash
codex exec "Fix the bug in auth.js"
```

### Reading from stdin

Fully supported. Three cases:

1. **Explicit prompt string**: Used as-is
2. **`-` (dash)**: Forces reading from stdin (no terminal check)
3. **No prompt + piped stdin**: Reads from stdin with a "Reading prompt from stdin..." message
4. **No prompt + terminal**: Exits with error "No prompt provided"

```bash
# Explicit stdin
echo "Fix the bug" | codex exec -
# Piped stdin (auto-detected)
echo "Fix the bug" | codex exec
# File-based prompt
codex exec - < prompt.txt
```

### System Prompt / Instructions Customization

There is **no `--system-prompt` or `--instructions` CLI flag**. Instructions are layered through config:

#### Cross-CLI Mapping

| Codex | Claude Code | Behavior |
|---|---|---|
| `model_instructions_file` (config.toml) | `--system-prompt` (CLI flag) | **Replaces** base system prompt entirely |
| `developer_instructions` (config.toml) | `CLAUDE.md` | **Additive** — injected alongside base prompt |
| `AGENTS.md` (repo files) | `CLAUDE.md` (repo files) | **Additive** — project instructions loaded from repo |

Key difference: Codex's replacement is via a config file path, not a CLI flag. Both CLIs strongly discourage replacing the base prompt (it removes built-in tool usage instructions, safety behaviors, etc.).

#### `model_instructions_file` (config.toml)

```toml
model_instructions_file = "/path/to/custom-instructions.md"
```

Overrides the built-in base instructions. The source code warns: "Users are STRONGLY DISCOURAGED from using this field, as deviating from the instructions [breaks things]."

#### `developer_instructions` (config.toml)

```toml
developer_instructions = "Always use TypeScript. Follow our team coding standards."
```

Injected as a separate `developer` role message in the conversation. This is the recommended way to customize behavior without replacing base instructions.

#### `instructions` (config.toml)

```toml
instructions = "You are a helpful assistant for our team."
```

Combined with AGENTS.md content via `get_user_instructions()`. If both exist, they are joined with a `--- project-doc ---` separator.

#### `-c` flag for runtime override

```bash
codex exec -c 'developer_instructions="Always write tests"' "Add a login feature"
codex exec -c 'model_instructions_file="/tmp/custom.md"' "Review this code"
```

### AGENTS.md Loading

**Primary file: `AGENTS.md`** (constant `DEFAULT_PROJECT_DOC_FILENAME`).
**Override file: `AGENTS.override.md`** (constant `LOCAL_PROJECT_DOC_FILENAME`, checked first).
**Fallback filenames**: Configurable via `project_doc_fallback_filenames` in config.toml.

Discovery process:
1. Walk upward from CWD to find project root (default marker: `.git`)
2. Collect every `AGENTS.override.md` or `AGENTS.md` from project root down to CWD
3. Concatenate in order (root first, CWD last)
4. Subject to `project_doc_max_bytes` budget (truncates if exceeded)
5. Deeper files override higher-level files

There is **no `CODEX.md`** support. The file must be named `AGENTS.md` (or `AGENTS.override.md`, or a configured fallback).

### `~/.codex/AGENTS.md`

User-level AGENTS.md — loaded as global instructions (not project-scoped). Serves the same role as `~/.claude/CLAUDE.md` in Claude Code.

---

## 2. Skills, Agents, Plugins, Custom Tools

### Skills

Skills are the primary extensibility mechanism. Discovery locations (highest priority first):

| Location | Scope |
|---|---|
| `.agents/skills/` (repo, from root to CWD) | Repo |
| `$CODEX_HOME/skills/` (`~/.codex/skills/`) | User (legacy) |
| `$HOME/.agents/skills/` | User (new) |
| `$CODEX_HOME/skills/.system/` | System |
| `/etc/codex/skills/` | Admin (Unix) |
| Plugin-bundled skills | Via plugin system (v0.110.0+) |

**Skill format**: Each skill is a directory containing a required `SKILL.md` file:

```
skill-name/
  SKILL.md          # Required: YAML frontmatter (name + description) + markdown body
  agents/
    openai.yaml     # Optional: UI metadata, permission profiles
  scripts/          # Optional: executable code
  references/       # Optional: documentation loaded into context on demand
  assets/           # Optional: output resources
```

**Invocation**: Skills are triggered by mentioning them with `$skill-name` in the prompt, or via `@plugin` for plugin-bundled skills. The `description` field in frontmatter is always in context (~100 words) for matching.

**Runtime behavior:**
- **Live skill updates** (v0.97.0): Skill file changes are detected and reloaded without restarting (10s debounce).
- **Permission profiles** (v0.102.0): Skills can declare permission profiles in `openai.yaml` metadata, controlling sandbox access for skill scripts.
- **Skill policy** (v0.99.0): `allow_implicit_invocation` field controls whether a skill can be triggered without explicit user mention.
- **Skill-scoped network overrides** (v0.115.0): Skills can override managed network domain allowlists in their config.
- **Disable system skills** (v0.114.0): Config switch to disable all bundled system skills entirely.

### System Skills (pre-installed)

System skills ship with Codex (can be disabled via config):
1. **openai-docs** — OpenAI documentation lookup via MCP
2. **skill-creator** — Guide for creating new skills
3. **skill-installer** — Install skills from GitHub (curated at `github.com/openai/skills`)
4. **plugin-creator** (v0.117.0) — Guide for creating new plugins

### Plugins (v0.110.0+)

Plugins are a first-class extensibility system that bundles skills, MCP entries, and app connectors.

**Management:**
- `/plugins` TUI menu — browse, install, remove plugins (v0.117.0)
- Product-scoped plugins synced at startup (v0.117.0)
- Plugin install/uninstall/read/list API endpoints (v0.113.0+)
- `@plugin` mentions in chat — auto-includes associated MCP/app/skill context (v0.112.0)
- `$` mention picker labels Skills, Apps, and Plugins; surfaces plugins first (v0.114.0)

**Plugin sources:**
- Local marketplace (`marketplace.json` + local directory)
- Curated marketplace (v0.113.0)
- Config-declared plugins

### Agents (Multi-Agent v2)

Multi-agent workflows have been significantly reworked since v0.102.0.

**Tools:**

| Tool | Purpose |
|---|---|
| `spawn_agent` | Spawn a sub-agent |
| `spawn_agents_on_csv` | Fan out work from a CSV with progress/ETA |
| `send_input` | Send message to a running agent |
| `wait_agent` | Wait for an agent to complete (renamed from `wait` in v0.115.0) |
| `close_agent` | Close a sub-agent (renamed in v0.116.0) |

**Key capabilities:**
- **Path-based addresses** like `/root/agent_a` (v0.117.0) — replaces UUIDs
- **Customizable roles** via `[agents]` config section (v0.102.0)
- **Structured inter-agent messaging** (v0.117.0)
- **Agent listing** (v0.117.0)
- **Ordinal nicknames** with role-labeled handoff context (v0.110.0)
- **Fork thread into sub-agents** (v0.107.0)
- **Subagents inherit sandbox and network rules** reliably (v0.115.0)
- **`/agent`** / **`/multiagent`** TUI command to enable/manage (v0.110.0)
- **Request permissions tool** (v0.113.0): Running agents can request additional permissions at runtime; permissions persist across turns.

### MCP Integration

Full MCP support via `codex mcp add`:

```bash
# Stdio transport
codex mcp add my-tool -- my-command --arg1

# Streamable HTTP transport
codex mcp add my-api --url https://example.com/mcp

# With auth
codex mcp add my-api --url https://example.com/mcp --bearer-token-env-var MY_TOKEN
```

MCP servers are configured in `~/.codex/config.toml` under `[mcp_servers]`. Skills can declare MCP dependencies in `openai.yaml` for auto-install.

**Approval and security:**
- **Session-scoped "Allow and remember"** (v0.97.0): Auto-approve repeated calls to the same MCP tool during the session.
- **Destructive MCP tool calls** require approval (v0.105.0).
- **MCP elicitation** (v0.111.0): Structured request/response flow for MCP input dialogs.
- **OAuth `oauth_resource` forwarding** (v0.107.0): Servers requiring a `resource` parameter are supported.
- **Configurable OAuth callback URL** (v0.105.0).

### Rules

`~/.codex/rules/default.rules` contains command-level approval rules:

```
prefix_rule(pattern=["git", "add"], decision="allow")
prefix_rule(pattern=["pytest", "tests", "-q"], decision="allow")
```

Syntax errors in rules files are now reported (v0.102.0). Model-suggested rules are restricted (v0.102.0).

### Hooks (v0.114.0+, Experimental)

Codex now has an experimental hooks engine. **Not available on Windows.**

| Event | Version | Purpose |
|---|---|---|
| `SessionStart` | v0.114.0 | Fires at session start |
| `Stop` | v0.114.0 | Fires at session end |
| `UserPromptSubmit` | v0.116.0 | Block or augment prompts before execution |
| `PreToolUse` | v0.117.0 | Non-streaming, shell-only — fires before tool execution |
| `PostToolUse` | v0.117.0 | Non-streaming, shell-only — fires after tool execution |

**Limitations:** Pre/PostToolUse are non-streaming and shell-only. The hooks system is experimental and not available on Windows.

---

## 3. The `/review` Command

### Entry Points

```bash
codex review [OPTIONS] [PROMPT]      # Top-level subcommand
codex exec review [OPTIONS] [PROMPT] # Under exec (non-interactive)
# Interactive: /review in the TUI
```

### Review Targets

Four mutually exclusive modes:

| Flag | Target | Prompt Generated |
|---|---|---|
| `--uncommitted` | Staged + unstaged + untracked | "Review the current code changes..." |
| `--base <BRANCH>` | Diff against base branch | "Review the code changes against the base branch '{branch}'..." + merge-base SHA |
| `--commit <SHA>` | Single commit | "Review the code changes introduced by commit {sha}..." |
| `[PROMPT]` (positional) | Custom instructions | User's text verbatim |

### Review System Prompt

The full review system prompt is at `codex-rs/core/review_prompt.md` (compiled into the binary via `include_str!`). Key aspects:

1. **Bug-focused**: Only flag issues that "meaningfully impact accuracy, performance, security, or maintainability"
2. **Calibrated severity**: Priority levels P0-P3 with clear definitions
3. **Structured JSON output**: Required output schema with `findings[]`, `overall_correctness`, `overall_explanation`
4. **Each finding has**: `title`, `body` (markdown), `confidence_score`, `priority`, `code_location` (file + line range)
5. **Conservative**: "If there is no finding that a person would definitely love to see and fix, prefer outputting no findings"
6. **No code suggestions > 3 lines**: Keeps comments concise

### Review Architecture

The review runs as a **sub-agent conversation** (`run_codex_thread_one_shot`):
- Separate config: web search disabled, sub-agents disabled, approval set to Never
- Uses `review_model` from config (falls back to current model)
- The review prompt replaces the normal base instructions
- Output is parsed as JSON `ReviewOutputEvent`, with fallback to plain text

### Review Output Format

```json
{
  "findings": [
    {
      "title": "[P1] Un-padding slices along wrong tensor dimensions",
      "body": "Markdown explanation of why this is a problem...",
      "confidence_score": 0.9,
      "priority": 1,
      "code_location": {
        "absolute_file_path": "/path/to/file.py",
        "line_range": {"start": 42, "end": 45}
      }
    }
  ],
  "overall_correctness": "patch is correct",
  "overall_explanation": "The patch is correct with one minor issue...",
  "overall_confidence_score": 0.85
}
```

---

## 4. Streaming Output

### `--json` Flag

`codex exec --json` produces **streaming JSONL** (one JSON object per line) to stdout. This is event-level streaming, not token-by-token.

### JSONL Event Types

```jsonl
{"type": "thread.started", "thread_id": "uuid-here"}
{"type": "turn.started"}
{"type": "item.started", "item": {"id": "...", "type": "command_execution", ...}}
{"type": "item.updated", "item": {"id": "...", "type": "command_execution", ...}}
{"type": "item.completed", "item": {"id": "...", "type": "command_execution", ...}}
{"type": "item.completed", "item": {"id": "...", "type": "agent_message", "text": "..."}}
{"type": "turn.completed", "usage": {"input_tokens": 1234, "cached_input_tokens": 500, "output_tokens": 456}}
```

### Item Types

| Type | Description |
|---|---|
| `agent_message` | Model's text response (final) |
| `reasoning` | Model's reasoning summary |
| `command_execution` | Shell command with `command`, `aggregated_output`, `exit_code`, `status` |
| `file_change` | File modifications with `changes[]` and `status` |
| `mcp_tool_call` | MCP tool invocation |
| `dynamic_tool_call` | Dynamic tool invocation (v0.106.0) — e.g., plugin or app-provided tools |
| `collab_tool_call` | Collaboration tool call (sub-agents) |
| `web_search` | Web search request |
| `todo_list` | Agent's running task list |
| `error` | Non-fatal error |

Items flow through: `item.started` → `item.updated` (0..N) → `item.completed`

There is **no token-by-token streaming** in `--json` output. The agent message is emitted only when complete.

**Multimodal tool output** (v0.107.0): Custom tools can return structured content including images, not just plain text. This surfaces in JSONL events as richer `item` payloads.

### `-o` / `--output-last-message`

Writes the final agent message text to a file:
```bash
codex exec -o /tmp/result.md "Explain this code"
```

**Flag positioning** (v0.105.0): `-o` now parses correctly when placed after subcommands like `resume`:
```bash
codex exec resume <ID> -o result.md "Continue"
```

### `--output-schema`

```bash
codex exec --output-schema schema.json "Analyze this codebase"
```

Forces the model to produce JSON conforming to the provided JSON Schema.

---

## 5. Multi-turn Support

### `codex exec` is Single-Turn

`codex exec` runs one turn and exits. The flow: start thread → start turn → process events → shutdown.

**Architecture note** (v0.117.0): `codex exec` now runs on the embedded in-process app-server internally. This was a gradual migration (v0.113.0 → v0.117.0). The external behavior is unchanged — same flags, same JSONL output — but the underlying execution engine now shares code with the interactive TUI, improving consistency.

### `codex exec resume`

Resume a previous session in non-interactive mode:

```bash
codex exec resume <SESSION_ID> "Continue with the next step"
codex exec resume --last "Now fix the tests"
```

This enables sequential multi-turn via scripting:
```bash
THREAD_ID=$(codex exec --json "Start a plan" | jq -r 'select(.type=="thread.started") | .thread_id')
codex exec resume "$THREAD_ID" "Implement step 1"
codex exec resume "$THREAD_ID" "Implement step 2"
```

**Persisted model on resume** (v0.116.0): Resumed threads automatically reuse the model and reasoning effort from the original session. You don't need to re-specify `-m` or `-c 'model_reasoning_effort=...'` on resume.

**`--ephemeral`** (v0.99.0): Properly wired into `codex exec` — prevents the thread from being saved to disk.

**Non-interactive resume filter** (v0.117.0): Resume can filter threads in non-interactive mode, useful for scripted workflows that need to find a specific thread.

### Thread ID vs Claude Code Session ID

Functionally equivalent — both are UUID conversation identifiers.

| | Codex | Claude Code |
|---|---|---|
| **Term** | Thread ID | Session ID |
| **Storage** | `~/.codex/sessions/` | `~/.claude/projects/<path>/sessions/` |
| **Resume (non-interactive)** | `codex exec resume <ID>` | `claude --resume <ID>` |
| **Resume last** | `codex exec resume --last` | `claude --continue` |
| **Fork** | `codex fork <ID>` | No direct equivalent |
| **Programmatic discovery** | JSONL `thread.started` event | Session files on disk |

---

## 6. Additional Configuration

### Reasoning Effort (`model_reasoning_effort`)

Controls how deeply the model reasons before responding. Only applies to reasoning-capable models (o-series, gpt-5+).

| Level | Description |
|---|---|
| `minimal` | Least reasoning; closest to pure instruction-following |
| `low` | Light reasoning |
| `medium` | **Default** |
| `high` | Deep reasoning |
| `xhigh` | Extra high — maximum reasoning depth |

Three ways to set it:

```bash
# 1. Runtime override via -c flag
codex exec -c 'model_reasoning_effort="high"' "Review this code"

# 2. Combined with model flag
codex exec -m gpt-5 -c 'model_reasoning_effort="xhigh"' "Analyze this architecture"

# 3. Via config.toml (global default)
# model_reasoning_effort = "high"
```

Interactive TUI: `/model` command lets you change model and reasoning effort mid-conversation.

### Service Tier (`service_tier`)

Controls API infrastructure routing. Independent of reasoning effort — can be combined with any effort level. When omitted, Codex uses the default API tier.

| Value | Description |
|---|---|
| `fast` | Lower-latency responses, same model and reasoning |
| `flex` | ~50% cheaper than default tier, higher latency — good for batch/background work |

```bash
# Runtime via -c flag
codex exec -c 'service_tier="fast"' "Quick task"
codex exec -c 'service_tier="flex"' "Background analysis"

# Combined with reasoning effort
codex exec -c 'service_tier="fast"' -c 'model_reasoning_effort="high"' "Review this"

# Via config.toml (global default)
service_tier = "fast"
```

Interactive TUI: `/fast` toggles fast mode. Fast mode is **enabled by default** (v0.111.0). The TUI header shows whether the session is running in Fast or Standard mode.

### Configuration Profiles

```toml
[profiles.review]
model = "o3"
model_reasoning_effort = "high"

[profiles.deep]
model = "gpt-5"
model_reasoning_effort = "xhigh"

[profiles.fast]
model = "gpt-4.1-mini"
```

```bash
codex exec -p review "Review this code"
codex exec -p deep "Analyze this complex architecture"
```

### Approval Policies

| Policy | Behavior |
|---|---|
| `untrusted` | Only run "trusted" commands without asking |
| `on-request` | Model decides when to ask |
| `never` | Never ask (default for exec mode) |
| `granular` | Auto-reject specific approval prompt types without turning approvals off entirely (v0.115.0, renamed from `reject`) |

**Note:** `on-failure` is deprecated (v0.102.0).

**Smart Approvals / Guardian** (v0.113.0+): A guardian subagent can automatically review approval requests, reducing repeated manual approvals on follow-ups. Reuses the same guardian session across approvals (v0.115.0).

### Sandbox Modes

| Mode | Behavior | Writable paths |
|---|---|---|
| `read-only` | No writes | None |
| `workspace-write` | Write only to workspace | workdir, `/tmp`, `$TMPDIR`, `~/.codex/memories` |
| `danger-full-access` | No restrictions | Everything |

**Sandbox evolution (v0.100.0+):**
- **`ReadOnlyAccess` policy shape** (v0.100.0): Configurable read access level within read-only mode.
- **Bubblewrap (`bwrap`)** is the default Linux sandbox (v0.115.0), replacing the legacy Landlock path.
- **Split filesystem/network policies** (v0.113.0): Granular control over filesystem and network access independently via the permission-profile config language.
- **`request_permissions` tool** (v0.113.0): Running turns can request additional sandbox permissions at runtime; granted permissions persist across turns (v0.114.0).

`--full-auto` = `--sandbox workspace-write -a on-request`
`--dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) = no sandbox, no approvals

### Custom Prompts (`~/.codex/prompts/`)

`.md` files with optional YAML frontmatter (`description`, `argument-hint`). Appear as slash commands in the TUI.

### Review Model Override

```toml
review_model = "o3"  # Use a different model for reviews
```

### Additional Config Options (v0.97.0+)

| Config key | Version | Purpose |
|---|---|---|
| `command_attribution` | v0.103.0 | Control co-author git attribution (`default`, custom label, or `disable`) — uses `prepare-commit-msg` hook |
| `openai_base_url` | v0.115.0 | Override built-in OpenAI provider URL |
| `allow_login_shell` | v0.105.0 | Allow login shell for command execution |
| `log_dir` | v0.97.0 | Redirect log output (supports `-c` overrides) |
| `memories` section | v0.107.0 | Configure memory behavior (workspace-scoped writes, extraction mode) |
| `disable_system_skills` | v0.114.0 | Disable all bundled system skills |
| `web_search` | v0.113.0 | Full tool configuration (filters, location), not just on/off |

### Memory System (v0.97.0+)

Codex maintains persistent thread memories across sessions:

- `/m_update` / `/m_drop` TUI commands for managing memories (v0.100.0)
- `codex debug clear-memories` to hard-wipe memory state (v0.107.0)
- Workspace-scoped writes — memories tied to project context (v0.110.0)
- Diff-based forgetting and usage-aware selection (v0.106.0)
- Memory citations in agent messages (v0.116.0)

### TUI Commands (v0.97.0+)

| Command | Version | Purpose |
|---|---|---|
| `/debug-config` | v0.97.0 | Inspect effective configuration |
| `/statusline` | v0.99.0 | Configure which metadata appears in TUI footer |
| `/copy` | v0.105.0 | Copy latest complete assistant reply |
| `/clear` | v0.105.0 | Clear screen (keeps context) or start fresh chat |
| `/theme` | v0.105.0 | Syntax highlighting theme picker with live preview |
| `/title` | v0.117.0 | Terminal title configuration |
| `/plugins` | v0.117.0 | Browse, install, remove plugins |
| `/agent` / `/multiagent` | v0.110.0 | Enable and manage multi-agent workflows |
| `/m_update` / `/m_drop` | v0.100.0 | Memory management |
| `/fast` | v0.110.0 | Toggle fast/standard service tier (persisted) |

### Codex as MCP Server

```bash
codex mcp-server  # Starts Codex as an MCP server (stdio transport)
```

---

## 7. Cross-CLI Comparison: Claude Code vs Codex

### Commands

| Capability | Claude Code | Codex | Notes |
|---|---|---|---|
| Interactive session | `claude` | `codex` | Both open a TUI |
| Non-interactive | `claude -p "query"` | `codex exec "query"` | CC uses `-p` flag, Codex uses `exec` |
| Piped input | `cat file \| claude -p` | `cat file \| codex exec` | Both support stdin |
| Continue last | `claude -c` | `codex exec resume --last` | |
| Resume by ID | `claude -r <id>` | `codex exec resume <id>` | |
| Fork session | `claude --resume <id> --fork-session` | `codex fork <id>` | |
| Code review | `/simplify` (v2.1.63) — no `codex review` equivalent | `codex review` / `codex exec review` | |
| Named sessions | `-n`/`--name` (v2.1.76) | Not available | |
| Recurring prompts | `/loop 5m <prompt>` (v2.1.71) | Not available | |
| Cron scheduling | `CronCreate` tool (v2.1.71) | Not available | |
| Effort control | `--effort high` (v2.1.81) | `-c 'model_reasoning_effort="high"'` | |
| MCP management | `claude mcp` | `codex mcp` | Both support add/remove/list |

### System Prompt & Instructions

| Capability | Claude Code | Codex |
|---|---|---|
| **Replace** system prompt | `--system-prompt "text"` | `-c 'model_instructions_file="/path"'` |
| **Append** to system prompt | `--append-system-prompt "text"` | `-c 'developer_instructions="text"'` |
| Project instructions | `CLAUDE.md` (walks root→CWD) | `AGENTS.md` (walks root→CWD) |
| Global instructions | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` |

### Output & Streaming

| Capability | Claude Code | Codex |
|---|---|---|
| JSON output | `--output-format json` | `--json` (streaming JSONL) |
| Output to file | No equivalent | `-o <file>` |
| Structured output | `--json-schema '{...}'` (inline) | `--output-schema schema.json` (file) |
| Session persistence | `--no-session-persistence` | `--ephemeral` |

### Hooks Comparison

| Capability | Claude Code | Codex |
|---|---|---|
| Hook system | Mature — 15+ event types, HTTP hooks, conditional `if` | Experimental (v0.114.0+) — 5 events, not on Windows |
| `SessionStart` / `Stop` | Yes | Yes (v0.114.0) |
| `PreToolUse` / `PostToolUse` | Yes (all tools, streaming) | Shell-only, non-streaming (v0.117.0) |
| `UserPromptSubmit` | Yes | Yes (v0.116.0) |
| `CwdChanged` / `FileChanged` | Yes (v2.1.83) | No |
| `TaskCreated` / `PostCompact` | Yes | No |
| Conditional filtering | `if` field (v2.1.85) | No |
| HTTP hooks | Yes (v2.1.63) | No |

### Features Unique to Codex

OS-level sandbox (bubblewrap/seatbelt), config profiles, built-in code review (`codex review`), JS REPL (`js_repl`), Smart Approvals guardian subagent, `request_permissions` tool, Codex-as-MCP-server, review model override, desktop app, voice transcription (hold spacebar), image generation, code mode (experimental), memory system (`/m_update`, `/m_drop`), `ReadOnlyAccess` sandbox shape, split filesystem/network policies, `granular` approval policy.

### Features Unique to Claude Code

Chrome browser integration, remote control, web sessions, agent teams, `--channels` (MCP message relay), PR-linked sessions, named sessions (`-n`/`--name`), budget controls (`--max-budget-usd`), token-level streaming, append system prompt, recurring prompts (`/loop`), cron scheduling (`CronCreate/Delete/List`), effort frontmatter for skills/agents, conditional hooks (`if` field), voice input (20 languages), `--bare` mode (CI isolation), 1M context window (Opus 4.6).

### Features Now Shared (Previously Unique)

| Feature | Claude Code | Codex |
|---|---|---|
| Plugin system | Plugins since v1.x | Plugins since v0.110.0 |
| MCP elicitation | v2.1.76 | v0.111.0 |
| Hooks | Always had hooks | v0.114.0 (experimental) |
| Memory system | Auto-memory | v0.97.0 (full memory with slash commands) |
| Theme/syntax highlighting | Default | `/theme` picker (v0.105.0) |

---

## 8. Headless Mode: `codex exec` Deep Dive

Comprehensive reference for non-interactive `codex exec` usage in automation pipelines. Tested against v0.116.0, updated through v0.117.0.

### `codex exec` Capabilities Summary

| Capability | Flag | Status |
|---|---|---|
| Single-turn prompt | `codex exec "prompt"` | Stable |
| Stdin prompt | `codex exec -` or `echo "prompt" \| codex exec` | Stable |
| Structured output | `--output-schema schema.json` | Stable |
| Output to file | `-o <file>` / `--output-last-message <file>` | Stable (see caveats) |
| Streaming JSONL | `--json` | Stable |
| Ephemeral (no persistence) | `--ephemeral` | Stable (wired v0.99.0) |
| Config profile | `-p <profile>` / `--profile <profile>` | Stable (fixed v0.115.0) |
| Model selection | `-m <model>` | Stable |
| Reasoning effort | `-c 'model_reasoning_effort="high"'` | Stable |
| Service tier | `-c 'service_tier="fast"'` or `"flex"` | Stable |
| Sandbox control | `--sandbox <mode>` | Stable |
| Approval policy | `-a <policy>` | Stable |
| Resume | `codex exec resume <ID> "prompt"` | Stable |
| Resume last | `codex exec resume --last "prompt"` | Stable |
| Code review | `codex exec review --base <sha>` | Stable (but output capture broken — see below) |

### `codex exec` Behavioral Notes

**Unified exec engine** (v0.96.0+): Shell processes spawned by `codex exec` use a unified execution engine on all non-Windows platforms. Processes persist across turns (v0.99.0) — meaning the shell environment is stable for multi-turn `exec resume` sessions.

**App-server backend** (v0.117.0): `codex exec` now runs on the embedded in-process app-server internally. External behavior is unchanged — same flags, same JSONL output — but the underlying engine now shares code with the interactive TUI.

**Profile handling** (v0.115.0): `codex exec --profile <name>` now correctly preserves profile-scoped settings (model, reasoning effort, sandbox, etc.) when starting or resuming a thread. Previously, profile settings were silently dropped.

**Persisted model on resume** (v0.116.0): Resumed threads reuse the model and reasoning effort from the original session. No need to re-specify `-m` or `-c 'model_reasoning_effort=...'` on resume.

**Shell commands concurrent with turns** (v0.99.0): Direct shell commands no longer interrupt in-flight turns — they execute concurrently. This is relevant for multi-turn pipelines.

**Analytics** (v0.113.0): Analytics/telemetry is now enabled in `codex exec` and `codex mcp-server` modes.

**Hooks interact with exec mode** (v0.116.0+): `UserPromptSubmit` hooks can block or augment prompts before execution in exec mode. If the target environment has Codex hooks configured, they may intercept subprocess invocations. `PreToolUse` and `PostToolUse` hooks (v0.117.0, shell-only) also fire during exec.

**Full-buffer capture policy** (v0.117.0): New internal policy for capturing full exec output — improves output fidelity for long command executions.

### `codex exec review` — Output Capture Still Broken

As of v0.117.0, the output capture bug remains. [GitHub Issue #6432](https://github.com/openai/codex/issues/6432) is still OPEN.

| Method | Result |
|---|---|
| `-o <file>` | **Empty file.** Warning: `"no last agent message"`. Review sub-agent suppresses the regular agent_message. |
| `--output-schema` + `-o` | **Empty file.** `--output-schema` is ignored by the review sub-agent. |
| `--json` (JSONL to stdout) | **Works.** Agent message in `item.completed` event. But text is **prose**, not structured JSON. |
| `--json` + `--output-schema` | **Schema ignored.** Agent message is still prose. |

**Extraction pattern for `--json` output:**
```bash
codex exec --json review --base <sha> --ephemeral \
  | jq -rs '[.[] | select(.type=="item.completed" and .item.type=="agent_message") | .item.text] | last // ""'
```

### `codex exec` with Custom Prompt — Structured Output Works

The [OpenAI Codex Cookbook](https://developers.openai.com/cookbook/examples/codex/build_code_review_with_codex_sdk) demonstrates using `codex exec` (not `codex exec review`) with a custom review prompt piped via stdin:

```bash
codex exec \
  --output-schema review-schema.json \
  -o findings.json \
  --sandbox read-only \
  --ephemeral \
  - < review-prompt.md
```

**Tested and confirmed working.** The `-o` file contains valid JSON conforming to the schema. Both approaches (broken `codex exec review` and working `codex exec` custom prompt) found the same real bug in the same commit, confirming review quality parity.

### Codex's Built-In Review Rubric

The full system prompt lives at `codex-rs/core/review_prompt.md` (compiled into binary). Key elements:

- **8 bug criteria:** Must be introduced in the commit, discrete and actionable, not relying on unstated assumptions, author would likely fix it, etc.
- **8 comment guidelines:** Brief (1 paragraph max), no code > 3 lines, matter-of-fact tone, no flattery.
- **P0-P3 severity:**
  - P0: Drop everything. Blocking release/operations. Universal issues only.
  - P1: Urgent. Address in next cycle.
  - P2: Normal. Fix eventually.
  - P3: Low. Nice to have.
- **Conservative threshold:** "If there is no finding that a person would definitely love to see and fix, prefer outputting no findings."

This rubric can be reused verbatim in custom `codex exec` prompts to get the same review quality with reliable structured output.

### Structured Outputs Schema Constraints

When using `--output-schema`, the schema must follow [OpenAI Structured Outputs](https://developers.openai.com/docs/guides/structured-outputs) constraints:

**Required:**
- `additionalProperties: false` on all objects
- All fields must be in `required` (use `["string", "null"]` type for optional)

**Supported constraints (non-fine-tuned models):**
- String: `pattern`, `format` (date-time, email, uuid, etc.)
- Number: `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`
- Array: `minItems`, `maxItems`
- `enum`, `anyOf`

**Not supported (any model):**
- `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`/`then`/`else`

**Limits:**
- 5,000 object properties total, 10 levels nesting
- 1,000 enum values, 120K chars for names/enums/const values
- Output key ordering matches schema key ordering

### Recommended Headless Review Invocation

```bash
# Compose review prompt (Codex's rubric + custom context)
cat > review-prompt.md << 'EOF'
<Codex review rubric text>

## Your Task
Review code changes between merge base <sha> and HEAD.
Run `git diff <sha>..HEAD` to inspect the changes.
Write analysis to <output-dir>/analysis.md.
EOF

# Invoke with structured output
codex exec \
  --output-schema review-schema.json \
  -o findings.json \
  --sandbox workspace-write \
  --ephemeral \
  - < review-prompt.md
```

This gives: structured JSON guaranteed by schema, working `-o` output, full control over prompt/rubric, and Codex writing analysis docs to the specified location.

### Using Profiles for Headless Review

With profiles, you can preconfigure review settings in `config.toml` and invoke with a single flag:

```toml
# ~/.codex/config.toml
[profiles.review]
model = "o3"
model_reasoning_effort = "high"
service_tier = "fast"

[profiles.quick-review]
model = "gpt-5.3-codex"
model_reasoning_effort = "medium"
service_tier = "flex"
```

```bash
# Uses the review profile's model, effort, and tier
codex exec -p review \
  --output-schema review-schema.json \
  -o findings.json \
  --sandbox workspace-write \
  --ephemeral \
  - < review-prompt.md

# Cheaper/faster for less critical reviews
codex exec -p quick-review \
  --output-schema review-schema.json \
  -o findings.json \
  --sandbox read-only \
  --ephemeral \
  - < review-prompt.md
```

**Gotcha:** `codex exec --profile` was broken before v0.115.0 — profile settings were silently dropped. Ensure you're on v0.115.0+ for reliable profile usage.

### Headless Subprocess Isolation

Unlike Claude Code, Codex has OS-level sandboxing. For headless subprocess invocations:

| Concern | Codex approach |
|---|---|
| Filesystem isolation | `--sandbox read-only` (no writes) or `workspace-write` (only workdir + tmp) |
| Network isolation | Split filesystem/network sandbox policies (v0.113.0) |
| Hook interference | Hooks fire in exec mode (v0.116.0+) — if undesired, no flag to suppress |
| MCP isolation | MCP servers from `config.toml` load by default — no `--strict-mcp-config` equivalent |
| Approval suppression | `-a never` (never ask, auto-approve) — default for exec mode |
| Memory | `--ephemeral` prevents thread persistence; memories may still be read |
| Skill suppression | `disable_system_skills = true` in config (v0.114.0) — no CLI flag |

**Key difference from Claude Code:** Codex's `--sandbox` provides actual OS-level filesystem restrictions (seatbelt on macOS, bubblewrap on Linux). Claude Code's `--allowedTools` only controls which tools the model can use — the tools themselves have unrestricted filesystem access.

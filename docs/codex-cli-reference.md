# Codex CLI Reference

Reference guide for integrating the OpenAI Codex CLI into automation pipelines. Based on source code analysis of `github.com/openai/codex` and runtime testing against v0.116.0.

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

## 2. Skills, Agents, Custom Tools

### Skills

Skills are the primary extensibility mechanism. Discovery locations (highest priority first):

| Location | Scope |
|---|---|
| `.agents/skills/` (repo, from root to CWD) | Repo |
| `$CODEX_HOME/skills/` (`~/.codex/skills/`) | User (legacy) |
| `$HOME/.agents/skills/` | User (new) |
| `$CODEX_HOME/skills/.system/` | System |
| `/etc/codex/skills/` | Admin (Unix) |

**Skill format**: Each skill is a directory containing a required `SKILL.md` file:

```
skill-name/
  SKILL.md          # Required: YAML frontmatter (name + description) + markdown body
  agents/
    openai.yaml     # Optional: UI metadata
  scripts/          # Optional: executable code
  references/       # Optional: documentation loaded into context on demand
  assets/           # Optional: output resources
```

**Invocation**: Skills are triggered by mentioning them with `$skill-name` in the prompt. The `description` field in frontmatter is always in context (~100 words) for matching.

### System Skills (pre-installed)

Three system skills ship with Codex:
1. **openai-docs** — OpenAI documentation lookup via MCP
2. **skill-creator** — Guide for creating new skills
3. **skill-installer** — Install skills from GitHub (curated at `github.com/openai/skills`)

### Agents

Multi-agent support exists: `spawn_agent`, `spawn_agents_on_csv`, `resume_agent`, `send_input`, `close_agent`, `wait`. The `multi_agent` feature flag is `stable: true`.

Sub-agents are spawned via a `spawn_agent` tool. The system prompt includes: "Only use `spawn_agent` if and only if the user explicitly asks for sub-agents, delegation, or parallel agent work."

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

### Rules

`~/.codex/rules/default.rules` contains command-level approval rules:

```
prefix_rule(pattern=["git", "add"], decision="allow")
prefix_rule(pattern=["pytest", "tests", "-q"], decision="allow")
```

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
| `collab_tool_call` | Collaboration tool call (sub-agents) |
| `web_search` | Web search request |
| `todo_list` | Agent's running task list |
| `error` | Non-fatal error |

Items flow through: `item.started` → `item.updated` (0..N) → `item.completed`

There is **no token-by-token streaming** in `--json` output. The agent message is emitted only when complete.

### `-o` / `--output-last-message`

Writes the final agent message text to a file:
```bash
codex exec -o /tmp/result.md "Explain this code"
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

### Fast Mode (`service_tier`)

Routes requests to faster API infrastructure. Independent of reasoning effort — can be combined with any effort level. Trades potential quality/throughput differences for lower latency.

```bash
# Runtime via -c flag
codex exec -c 'service_tier="fast"' "Quick task"

# Combined with reasoning effort
codex exec -c 'service_tier="fast"' -c 'model_reasoning_effort="high"' "Review this"

# Via config.toml (global default)
service_tier = "fast"
```

Interactive TUI: `/fast` toggles fast mode. **Caution:** `/fast` currently writes to `config.toml` (global), not session-scoped — it affects all future sessions until toggled again.

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

### Sandbox Modes

| Mode | Behavior | Writable paths |
|---|---|---|
| `read-only` | No writes | None |
| `workspace-write` | Write only to workspace | workdir, `/tmp`, `$TMPDIR`, `~/.codex/memories` |
| `danger-full-access` | No restrictions | Everything |

`--full-auto` = `--sandbox workspace-write -a on-request`
`--dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) = no sandbox, no approvals

### Custom Prompts (`~/.codex/prompts/`)

`.md` files with optional YAML frontmatter (`description`, `argument-hint`). Appear as slash commands in the TUI.

### Review Model Override

```toml
review_model = "o3"  # Use a different model for reviews
```

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
| Code review | No built-in | `codex review` / `codex exec review` | |
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

### Features Unique to Codex

OS-level sandbox, config profiles, feature flags, built-in code review, personality system, cloud execution, Codex-as-MCP-server, web search toggle, review model override, desktop app.

### Features Unique to Claude Code

Chrome browser integration, remote control, web sessions, agent teams, channels, PR-linked sessions, named sessions, init hooks, permission prompt delegation, budget controls, token-level streaming, append system prompt.

---

## 8. Headless Code Review: Practical Findings (v0.116.0)

Tested against real commits. These findings supplement the source-code analysis.

### `codex exec review` — Output Capture Is Broken

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

Known issue — see [GitHub Issue #6432](https://github.com/openai/codex/issues/6432).

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

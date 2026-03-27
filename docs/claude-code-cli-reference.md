# Claude Code CLI Reference

Reference guide for integrating the Claude Code (CC) CLI into automation pipelines. Based on npm package source analysis of `@anthropic-ai/claude-code@2.1.81`, runtime testing, and production usage patterns from the cabrero project.

## Architecture Overview

The npm package (`@anthropic-ai/claude-code`) ships a Bun-compiled binary (`cli.js`) — a single 12MB JavaScript file bundled with the Bun runtime into a native executable. The installed binary lives at `~/.local/share/claude/versions/<version>` with a symlink at `~/.local/bin/claude`.

Two modes of operation:
- **Interactive** (`claude`) — TUI session with tool use, multi-turn conversation, and agentic loop
- **Non-interactive** (`claude -p` / `claude --print`) — stdin→stdout, single result, exits when done

Both modes share the same binary. `-p` / `--print` selects the non-interactive path.

---

## 1. Non-Interactive Mode (`-p` / `--print`)

### Inline Prompt

```bash
claude -p "Fix the bug in auth.js"
```

### Reading from stdin

```bash
# Piped stdin
echo "Fix the bug" | claude -p
# File-based prompt
claude -p < prompt.txt
# Explicit prompt with stdin context
echo "context data" | claude -p "Analyze this"
```

When both a prompt argument and stdin data are provided, the stdin content is included as context alongside the prompt.

### System Prompt Control

| Flag | Behavior |
|---|---|
| `--system-prompt "text"` | **Replaces** the entire system prompt (removes built-in instructions, tools guidance, CLAUDE.md) |
| `--system-prompt-file <path>` | Same as above, from a file |
| `--append-system-prompt "text"` | **Additive** — injected alongside the base prompt |
| `--append-system-prompt-file <path>` | Same as above, from a file |

**Key difference from Codex:** CC provides direct CLI flags for prompt replacement/extension. Codex requires config.toml entries or `-c` flag overrides.

### Output Formats

| Flag | Output |
|---|---|
| `--output-format text` | Plain text result (default) |
| `--output-format json` | Single JSON object with metadata (see [JSON Output Structure](#json-output-structure)) |
| `--output-format stream-json` | Realtime streaming JSONL events |

### Structured Output

```bash
claude -p --output-format json \
  --json-schema '{"type":"object","properties":{"findings":{"type":"array","items":{"type":"object","properties":{"title":{"type":"string"},"severity":{"type":"string"}},"required":["title","severity"]}}},"required":["findings"]}' \
  "Review this code"
```

The structured output appears in the `structured_output` field of the JSON response. The `result` field contains the model's text response; `structured_output` contains the schema-validated JSON.

**Mechanism:** CC implements structured output via a **tool-use extraction pattern**, not constrained decoding. Internally, the model is given a tool whose input schema matches your `--json-schema`. The model calls this tool to produce the structured output, then responds with a text summary. This means:

- `num_turns` will be 2+ (one for the tool call, one for the text response) even for simple prompts
- There is an **extra turn of cost and latency** compared to Codex's `--output-schema`, which uses OpenAI's constrained decoding in a single generation pass
- For long-running tasks (e.g., a 30-minute code review), this overhead is negligible; for high-volume short tasks, it adds up

**Interaction with `--tools ""`:** When `--tools ""` disables all tools, the structured output tool is still available — `--json-schema` takes precedence. The model can produce structured output even with no other tools enabled.

**Schema format:** Inline JSON string (not a file path). For complex schemas, use shell variables or heredocs:

```bash
SCHEMA=$(cat schema.json)
echo "prompt" | claude -p --output-format json --json-schema "$SCHEMA"
```

**Schema constraints:** Standard JSON Schema — no `additionalProperties: false` requirement (unlike OpenAI Structured Outputs which requires it on all objects).

### Tool Control

Two distinct mechanisms — `--tools` controls which tools **exist**, `--allowedTools` controls which are **pre-approved** (no permission prompt):

| Flag | Effect |
|---|---|
| `--tools ""` | Disable ALL tools — pure text-in/text-out, no agentic behavior |
| `--tools "Bash,Read,Grep"` | Only these tools exist in the session |
| `--tools "default"` | All built-in tools (default) |
| `--allowedTools "Read,Bash(git diff *)"` | Pre-approve specific tools/patterns (adds to allow list) |
| `--disallowedTools "Edit"` | Block specific tools |

### Built-in Tools Permission Classification

From the [official tools reference](https://code.claude.com/docs/en/tools-reference). Tools that require permission will trigger a prompt (or be auto-denied/approved depending on the permission mode).

**No permission required (always available):**

| Tool | Description |
|---|---|
| `Agent` | Spawn a subagent with its own context window |
| `Read` | Read file contents |
| `Grep` | Search for patterns in file contents |
| `Glob` | Find files by pattern matching |
| `AskUserQuestion` | Ask clarifying questions |
| `TaskCreate/Get/List/Output/Stop/Update` | Task management |
| `TodoWrite` | Session task checklist (non-interactive / Agent SDK) |
| `CronCreate/Delete/List` | Scheduled tasks within session |
| `EnterPlanMode` / `ExitWorktree` / `EnterWorktree` | Mode and worktree control |
| `ToolSearch` | Load deferred tools |
| `LSP` | Code intelligence via language servers |
| `ListMcpResourcesTool` / `ReadMcpResourceTool` | MCP resource access |

**Permission required:**

| Tool | Description |
|---|---|
| `Bash` | Execute shell commands |
| `Edit` | Targeted file edits |
| `Write` | Create or overwrite files |
| `NotebookEdit` | Modify Jupyter notebook cells |
| `Skill` | Execute a skill in the main conversation |
| `WebFetch` | Fetch content from a URL |
| `WebSearch` | Perform web searches |
| `ExitPlanMode` | Present a plan for approval |

**Key implication for subprocess design:** Spawning a subagent via `Agent` never requires permission. Only the subagent's *own tool use* (Bash, Edit, Write, etc.) is subject to permission checks — governed by the subagent's `permissionMode` and the parent session's permission rules.

**`--tools` vs `--allowedTools`:** `--tools` restricts the tool set. `--allowedTools` pre-approves tools within the available set (so they don't trigger permission prompts). For subprocess use, combine them with a permission mode (see below).

### Permission Modes

| Mode | Flag | Behavior |
|---|---|---|
| `default` | `--permission-mode default` | Prompts for each tool on first use |
| `acceptEdits` | `--permission-mode acceptEdits` | Auto-approves file edits for the session |
| `plan` | `--permission-mode plan` | Read-only — no modifications or commands |
| `dontAsk` | `--permission-mode dontAsk` | **Auto-denies** tools unless pre-approved via `--allowedTools` |
| `bypassPermissions` | `--dangerously-skip-permissions` | **Auto-approves** all tools (still protects `.git`, `.claude`, `.vscode`, `.idea` from writes) |

**For subprocess invocations**, two safe patterns:

1. **Allowlist approach** — `--permission-mode dontAsk` + `--allowedTools "Read,Grep,Glob,Write,Bash(git diff *)"`. Only explicitly listed tools work. Everything else is silently denied. Most restrictive.

2. **Bypass approach** — `--dangerously-skip-permissions` + `--tools "Read,Grep,Glob,Write,Bash"`. All available tools are auto-approved, but `--tools` restricts which tools exist. Simpler, but `--tools` doesn't support per-command scoping like `--allowedTools` does.

**Key gotcha:** `--permission-mode dontAsk` **without** `--allowedTools` denies everything — the subprocess will fail on its first tool call. Always pair `dontAsk` with pre-approved tools.

### Session Control

| Flag | Effect |
|---|---|
| `--no-session-persistence` | Don't save transcript to disk |
| `--session-id <uuid>` | Use a specific session ID |
| `-r, --resume <id>` | Resume a previous session |
| `-c, --continue` | Continue the most recent session |
| `--fork-session` | Fork when resuming (new ID, same context) |

### Budget Control

```bash
claude -p --max-budget-usd 0.50 "Review this code"
```

Caps API spend for the session. Only works with `--print`.

---

## 2. JSON Output Structure

When using `--output-format json`, CC returns a single JSON object:

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 19948,
  "duration_api_ms": 3342,
  "num_turns": 1,
  "result": "The model's text response",
  "stop_reason": "end_turn",
  "session_id": "uuid",
  "total_cost_usd": 0.2136,
  "usage": {
    "input_tokens": 2,
    "cache_creation_input_tokens": 34159,
    "cache_read_input_tokens": 0,
    "output_tokens": 4,
    "service_tier": "standard"
  },
  "structured_output": { "...schema-validated JSON..." },
  "permission_denials": [],
  "fast_mode_state": "off"
}
```

Key fields for pipeline integration:
- `result` — The model's text response (always present)
- `structured_output` — Schema-validated JSON (only when `--json-schema` is used)
- `is_error` — Whether the session errored
- `total_cost_usd` — Session cost for telemetry/budgeting
- `num_turns` — Number of model turns (1 for tool-free, more for agentic)
- `permission_denials` — Tools that were blocked by permission settings

---

## 3. Subprocess Invocation

### The `CLAUDECODE` Environment Variable

CC sets `CLAUDECODE=1` in the environment of every child process it spawns:
- Shell snapshots (environment capture at startup)
- Bash tool commands
- Teammate agent processes

This means any process launched from within a CC session inherits `CLAUDECODE=1`.

### Nesting Guard — Removed in v2.1.81

**v2.1.62 (historical):** CC checked `process.env.CLAUDECODE === "1"` at startup and refused to launch nested instances (with exceptions for `--team-name` invocations and safe subcommands like `--version`, `auth`, `mcp`). Notably, `-p` (print mode) was **blocked**.

**v2.1.81 (current):** The nesting guard has been **completely removed**. Source analysis of `cli.js` shows only 3 references to `CLAUDECODE` — all are writes (setting the env var on child processes). No code reads `CLAUDECODE` as a launch condition.

**Empirical verification:** Running `claude -p --output-format json` from within a CC session (where `CLAUDECODE=1` is set) succeeds with exit code 0 and returns valid results.

**Implication:** Spawning CC as a subprocess from within a CC session (e.g., via the Bash tool) works without needing to strip `CLAUDECODE` from the environment. This was not possible in v2.1.62.

### `--bare` Mode

Sets `CLAUDE_CODE_SIMPLE=1` internally. Designed for scripted and CI use. Skips auto-discovery of hooks, skills, plugins, MCP servers, auto-memory, CLAUDE.md, LSP, plugin sync, attribution, background prefetches, and keychain reads. **Will become the default for `-p` in a future release.**

**What `--bare` keeps:** Bash, Read, and Edit tools are still available. The model can read files, run shell commands, and edit code. Only auto-discovery and ambient configuration are stripped.

**What `--bare` skips that you can re-add explicitly:**

| To load | Use |
|---|---|
| System prompt additions | `--append-system-prompt`, `--append-system-prompt-file` |
| Settings | `--settings <file-or-json>` |
| MCP servers | `--mcp-config <file-or-json>` |
| Custom agents | `--agents <json>` |
| A plugin directory | `--plugin-dir <path>` |

**Auth limitation:** `--bare` skips OAuth and keychain reads. Anthropic authentication must come from `ANTHROPIC_API_KEY` or an `apiKeyHelper` in the JSON passed to `--settings`. Bedrock, Vertex, and Foundry use their usual provider credentials.

### Recommended Subprocess Isolation

#### `--bare` Limitations

`--bare` is designed for CI/scripts with `ANTHROPIC_API_KEY`. It has two limitations that make it unsuitable as a general-purpose subprocess isolation mechanism:

1. **Auth:** Skips OAuth and keychain reads. `apiKeyHelper` outputs go to `x-api-key` header — it cannot bridge OAuth tokens (which require `Authorization: Bearer`). Users on Claude subscriptions (OAuth) cannot use `--bare`.
2. **Tool set:** Restricts to Bash, Read, Edit only. Grep, Glob, and Write are not available. The model must use `grep`/`find` via Bash instead of the dedicated tools.

#### Flag-Based Isolation (Works with All Auth Methods)

Use individual flags to suppress ambient configuration while keeping the full tool set and existing auth:

```bash
claude -p \
  --output-format json \
  --permission-mode dontAsk \
  --allowedTools 'Read,Grep,Glob,Write,Bash(git diff *,git log *,git show *,git blame *)' \
  --settings '{"disableAllHooks": true}' \
  --mcp-config '{"mcpServers":{}}' \
  --strict-mcp-config \
  --disable-slash-commands \
  --model sonnet \
  < prompt.txt
```

This preserves:
- **OAuth/subscription auth** — keychain reads work normally
- **Full tool set** — Grep, Glob, Write available alongside Bash, Read, Edit
- **CLAUDE.md auto-discovery** — the subprocess gets project context automatically
- **Session persistence** — transcript is saved, can be inspected/resumed for debugging
- **Base system prompt** — model retains built-in tool usage instructions

This suppresses:
- **Hooks** — via `disableAllHooks` setting (prevents self-observation loops, TCC prompts)
- **MCP servers** — via empty config + strict mode (no Slack, Linear, etc.)
- **Skills** — via `--disable-slash-commands` (no skills fire inside subprocess)

#### Isolation Flags Explained

| Flag | Purpose |
|---|---|
| `--permission-mode dontAsk` | Auto-deny tools not in `--allowedTools` — no interactive prompts |
| `--allowedTools` | Pre-approve specific tools; pair with `dontAsk` to create an allowlist |
| `--settings '{"disableAllHooks": true}'` | Suppress all hooks |
| `--mcp-config '{"mcpServers":{}}'` | Empty MCP server list (**must** include `mcpServers` key — bare `{}` crashes CLI 2.1.59+) |
| `--strict-mcp-config` | Only servers from `--mcp-config`; ignores plugins, user settings, project `.mcp.json` |
| `--disable-slash-commands` | Disable all skills |
| `--model` | Explicit model selection — controls cost/quality |

**Not needed:**
- ~~`--no-session-persistence`~~ — session persistence is useful for debugging and inspection
- ~~`--system-prompt`~~ / ~~`--append-system-prompt`~~ — CLAUDE.md loads automatically; base prompt stays intact
- ~~`--bare`~~ — doesn't work with OAuth; restricts tool set too aggressively

#### Tool Scoping for Code Review

For review tasks that need filesystem and git access (not just text analysis), scope tools appropriately:

```
--allowedTools 'Read,Grep,Glob,Write,Bash(git diff *,git log *,git show *,git blame *)'
```

| Tool | Purpose in review |
|---|---|
| `Read` | Read file contents for context |
| `Grep` | Search code for patterns |
| `Glob` | Find files by name/pattern |
| `Write` | Write analysis docs to output directory |
| `Bash(git diff *)` | Inspect changes between commits |
| `Bash(git log *)` | View commit history |
| `Bash(git show *)` | View specific commits |
| `Bash(git blame *)` | Track line-level authorship |

**No sandbox equivalent:** CC has no filesystem-level sandboxing like Codex's `--sandbox workspace-write`. `--allowedTools` controls *which* tools are available, not *where* they can write. If `Write` is enabled, the model can write anywhere. Constrain via prompt instructions.

---

## 4. Settings System

### Settings Hierarchy (Precedence Order)

| # | Source | Location | Override method |
|---|---|---|---|
| 1 | **Managed** (highest) | `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS) | Cannot be overridden |
| 2 | **CLI arguments** | `--settings` flag | Session-specific |
| 3 | **Local** | `.claude/settings.local.json` | Machine-specific, gitignored |
| 4 | **Project** | `.claude/settings.json` | Shared via git |
| 5 | **User** | `~/.claude/settings.json` | Personal defaults |
| 6 | **Defaults** (lowest) | Built-in | CC's baseline |

The `--settings` flag is **additive** — it does not replace settings loaded from sources.

### `--setting-sources`

Controls which settings files load. Comma-separated: `user`, `project`, `local`.

```bash
claude --setting-sources "user" -p "query"  # Only load ~/.claude/settings.json
```

**Caveat:** Passing `""` (empty string) to skip all sources broke in CLI 2.1.59+ — do not rely on it. Use `--settings` with explicit overrides instead.

### Key Settings for Subprocess Isolation

```json
{
  "disableAllHooks": true,
  "alwaysThinkingEnabled": false,
  "enabledPlugins": {}
}
```

| Setting | Override works? | Notes |
|---|---|---|
| `disableAllHooks: true` | Yes | Boolean, clean override at precedence #2 |
| `alwaysThinkingEnabled: false` | Yes | Prevents extended thinking (controls cost/latency) |
| `enabledPlugins: {}` | Uncertain | Object merge semantics unclear (shallow vs deep). Mitigated by `--system-prompt` + `--strict-mcp-config` + `--disable-slash-commands` |

---

## 5. Model Selection and Effort

### Model Flag

```bash
claude -p --model sonnet "Quick task"
claude -p --model opus "Deep analysis"
claude -p --model haiku "Fast classification"
```

Accepts aliases (`sonnet`, `opus`, `haiku`) or full model IDs (`claude-sonnet-4-6`).

### Effort Levels

```bash
claude -p --effort high "Complex review"
```

| Level | Description |
|---|---|
| `low` | Minimal reasoning |
| `medium` | Standard (default) |
| `high` | Deep reasoning |
| `max` | Maximum reasoning depth |

### Fallback Model

```bash
claude -p --fallback-model sonnet --model opus "Task"
```

Automatically falls back to the specified model when the primary is overloaded. Only works with `--print`.

---

## 6. What Still Leaks Through

Even with full isolation flags, user settings files are still loaded (unless `--bare` is used, which breaks auth). Non-critical settings that may leak:

- `theme`, `cleanupPeriodDays`, and other cosmetic settings — no behavioral impact
- `enabledPlugins` — uncertain merge semantics, but mitigated by `--system-prompt` + `--strict-mcp-config` + `--disable-slash-commands` (plugins can't inject instructions, skills, or MCP servers)

The mitigation stack (`--system-prompt` replaces prompt, `--strict-mcp-config` blocks MCP, `--disable-slash-commands` blocks skills) effectively prevents plugin interference even if `enabledPlugins: {}` doesn't fully suppress plugin loading.

---

## 7. Cross-CLI Comparison: Claude Code vs Codex (Subprocess Focus)

### Invocation

| Capability | Claude Code | Codex |
|---|---|---|
| Non-interactive mode | `claude -p "query"` | `codex exec "query"` |
| Stdin prompt | `echo "prompt" \| claude -p` | `echo "prompt" \| codex exec -` |
| Structured output | `--json-schema '{...}'` (inline JSON) | `--output-schema schema.json` (file path) |
| JSON response | `--output-format json` (single object) | `--json` (streaming JSONL) |
| Output to file | Not available | `-o <file>` |
| System prompt replace | `--system-prompt "text"` (CLI flag) | `model_instructions_file` (config.toml) |
| System prompt append | `--append-system-prompt "text"` | `developer_instructions` (config.toml or `-c`) |
| Disable all tools | `--tools ""` | Not available (sandbox modes instead) |
| Model selection | `--model sonnet` | `-m gpt-5` |
| Reasoning effort | `--effort high` | `-c 'model_reasoning_effort="high"'` |
| Budget cap | `--max-budget-usd 0.50` | Not available |
| Session persistence | `--no-session-persistence` | `--ephemeral` |
| Nesting from parent session | Works (no guard in v2.1.81) | Works (no guard) |

### Isolation

| Mechanism | Claude Code | Codex |
|---|---|---|
| Single-flag isolation | `--bare` (needs API key; limited tool set — no Grep/Glob/Write) | N/A |
| Hook suppression | `--settings '{"disableAllHooks": true}'` | N/A (no hooks system) |
| MCP isolation | `--mcp-config '{"mcpServers":{}}' --strict-mcp-config` | N/A (MCP configured per-session) |
| Skill suppression | `--disable-slash-commands` | N/A |
| Permission control | `dontAsk` (deny unless allowed) or `bypassPermissions` (approve all) | `--approval never` |
| Sandbox | Not available — use `--tools` + `--allowedTools` for tool-level scoping | `--sandbox read-only` / `workspace-write` |
| Tool scoping | `--allowedTools 'Read,Grep,Write,Bash(git diff *)'` | Implicit via sandbox |

### Structured Output Differences

| Aspect | Claude Code | Codex |
|---|---|---|
| Schema location | Inline JSON string via `--json-schema` | File path via `--output-schema` |
| Mechanism | Tool-use extraction (model calls a schema-typed tool) | Constrained decoding (single generation pass) |
| Turns consumed | 2+ (tool call turn + text response turn) | 1 (schema enforced during generation) |
| Cost implication | Extra turn of input/output tokens | No overhead |
| Schema constraints | Standard JSON Schema | OpenAI Structured Outputs (requires `additionalProperties: false`, all fields in `required`) |
| Output location | `structured_output` field in JSON response | Written to `-o` file |
| Parsing | Read JSON response, extract `structured_output` | Read output file directly |

---

## 8. Python Subprocess Integration

Pattern for invoking CC from Python scripts (e.g., automation pipelines running within a CC session):

```python
import subprocess
import json

def invoke_claude_review(prompt_file, schema_file, model="sonnet", timeout=1800,
                         effort=None):
    """Invoke Claude Code CLI as a subprocess for code review.

    Uses flag-based isolation to suppress hooks, MCP, and skills while
    preserving OAuth auth, CLAUDE.md auto-discovery, full tool set,
    and session persistence.
    """
    with open(schema_file) as f:
        schema = f.read()

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", schema,
        "--permission-mode", "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob,Write,Bash(git diff *,git log *,git show *,git blame *)",
        "--settings", '{"disableAllHooks": true}',
        "--mcp-config", '{"mcpServers":{}}',
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--model", model,
    ]

    if effort:
        cmd.extend(["--effort", effort])

    with open(prompt_file) as f:
        prompt = f.read()

    # Run from repo root so the reviewer can access project files and CLAUDE.md
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        toplevel = None

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=toplevel or None,
    )

    if result.returncode != 0:
        return None, result.stderr

    response = json.loads(result.stdout)

    if response.get("is_error"):
        return None, response.get("result", "Unknown error")

    return response.get("structured_output", response.get("result")), None
```

**Key considerations:**
- No need to strip `CLAUDECODE` from the environment (nesting guard removed in v2.1.81)
- Works with OAuth/subscription auth — no `ANTHROPIC_API_KEY` required
- CLAUDE.md loads automatically — the reviewer gets project context for free
- Session is persisted — can inspect/resume for debugging
- `--json-schema` must be a JSON string, not a file path — read the schema file first
- The response is a single JSON object on stdout (not streaming JSONL like Codex)
- `timeout` should be generous — model inference + tool use can take minutes
- `cwd` should be the repo root so the reviewer can access project files
- `--allowedTools` scopes tool access — CC has no sandbox equivalent for filesystem-level restrictions

### Environment Variable Reference

Env vars that affect subprocess behavior (beyond standard Anthropic auth):

| Variable | Effect |
|---|---|
| `CLAUDECODE` | Set to `"1"` on all CC child processes. No longer checked as a nesting guard (v2.1.81+). |
| `CLAUDE_CODE_SIMPLE` | Set by `--bare`. Disables hooks, LSP, plugins, CLAUDE.md, auto-memory. Also skips keychain auth. |
| `CLAUDE_CODE_DONT_INHERIT_ENV` | When set, CC doesn't inherit the parent shell's environment snapshot. |
| `ANTHROPIC_API_KEY` | API key for direct Anthropic API auth (used by `--bare` mode). |
| `ANTHROPIC_BASE_URL` | Custom API endpoint. |
| `CLAUDE_CODE_USE_BEDROCK` | Use AWS Bedrock as the API provider. |
| `CLAUDE_CODE_USE_VERTEX` | Use Google Vertex AI as the API provider. |

---

## 9. Version History of Key Behaviors

| Version | Change | Impact |
|---|---|---|
| 2.1.59 | `--setting-sources ""` broken (empty stdout, process exits) | Cannot use empty string to skip settings files; use `--settings` overrides instead |
| 2.1.62 | Last version with active nesting guard | `CLAUDECODE=1` check + `process.exit(1)` for nested non-teammate invocations |
| 2.1.81 | Nesting guard removed | `-p` mode works from within CC sessions without environment stripping |
| 2.1.81 | `--json-schema` available | Structured output via inline JSON schema (returns `structured_output` in response) |
| 2.1.81 | `--bare` mode available | Single-flag isolation (hooks, MCP, plugins, skills, CLAUDE.md). Keeps only Bash/Read/Edit tools (no Grep/Glob/Write). Requires `ANTHROPIC_API_KEY` — skips OAuth/keychain; `apiKeyHelper` cannot bridge OAuth (outputs to `x-api-key` header, not `Authorization: Bearer`). Will become default for `-p` in a future release. |
| 2.1.81 | `--effort` flag available | Direct effort control (low/medium/high/max) |
| 2.1.81 | `--max-budget-usd` available | Session cost cap for `--print` mode |

---

## 10. Recommended Headless Review Invocation

For agentic code review pipelines where the reviewer explores the codebase, runs git commands, and writes analysis docs:

```bash
# 1. Prepare schema
SCHEMA='{"type":"object","properties":{"findings":{"type":"array","items":{"type":"object","properties":{"title":{"type":"string"},"body":{"type":"string"},"severity":{"type":"string","enum":["P0","P1","P2","P3"]},"location":{"type":"string"},"confidence":{"type":"number"}},"required":["title","body","severity","location","confidence"]}},"overall_correctness":{"type":"string"},"overall_explanation":{"type":"string"}},"required":["findings","overall_correctness","overall_explanation"]}'

# 2. Invoke CC with structured output and tool access
claude -p \
  --output-format json \
  --json-schema "$SCHEMA" \
  --permission-mode dontAsk \
  --allowedTools 'Read,Grep,Glob,Write,Bash(git diff *,git log *,git show *,git blame *)' \
  --settings '{"disableAllHooks": true}' \
  --mcp-config '{"mcpServers":{}}' \
  --strict-mcp-config \
  --disable-slash-commands \
  --model sonnet \
  < review-prompt.md
```

This gives:
- **Structured JSON** validated by schema (`structured_output` field in response)
- **Agentic review** — reviewer can read files, search code, run git commands, write analysis docs
- **Project context** — CLAUDE.md loads automatically (not using `--bare`)
- **Session persistence** — transcript saved for debugging/inspection
- **OAuth auth** — works with subscription accounts (no API key required)
- **Isolated execution** — hooks, MCP, and skills suppressed; tool access scoped to review needs
- **No sandbox** — `Write` and `Bash` are not filesystem-restricted; constrain via prompt instructions

---

## Provenance

- **Source analysis:** `@anthropic-ai/claude-code@2.1.81` npm package (`cli.js`, 12MB Bun bundle)
- **Runtime testing:** v2.1.81 on macOS (arm64), March 2026
- **Production patterns:** cabrero project subprocess isolation (v0.27+), pirategoat-bot headless review orchestration
- **Previous version analysis:** cabrero's `docs/subprocess-isolation.md` (v2.1.62 nesting guard documentation)

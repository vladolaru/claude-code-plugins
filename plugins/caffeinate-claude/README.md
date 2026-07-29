# caffeinate-claude

Keeps your Mac awake during Claude Code and Codex sessions using macOS's
built-in `caffeinate` command. Supports multiple tabs, handles crashes
gracefully with a 1-hour safety timeout, and cleans up automatically when all
sessions end.

## How it works

- **`UserPromptSubmit`** — Registers the session and starts `caffeinate -i -t 3600`
- **`Stop`** — Deregisters the session and kills caffeinate when no active sessions remain
- Each tab gets its own session marker (`$PPID`), so multiple tabs work without conflicts
- Both scripts prune markers for dead processes, handling crashes gracefully
- The 1-hour caffeinate timeout is a failsafe if the host exits without triggering the Stop hook

## Installation

### Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install caffeinate-claude@vladolaru-claude-code-plugins
```

### Codex

```bash
codex plugin marketplace add vladolaru/claude-code-plugins
codex plugin add caffeinate-claude@vladolaru-claude-code-plugins
```

Codex asks you to review and trust plugin hooks before running them.

## Credits

Based on [Preventing Mac Sleep During Claude Code Sessions](https://dev.ngockhuong.com/posts/preventing-mac-sleep-during-claude-code-sessions/) by [Khuong Lam](https://dev.ngockhuong.com).

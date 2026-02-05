# Browser Navigator Agent Design

**Date:** 2026-02-05
**Status:** Approved
**Author:** Vlad Olaru + Claude

## Overview

Create a `browser-navigator` agent that handles all browser automation tasks in isolation. The existing `browser-interaction` skill becomes a lightweight dispatcher to this agent.

### Goals

1. **Context savings** - Keep verbose snapshots out of main conversation
2. **Reliability** - Isolate browser failures from main session
3. **Delegation** - "Go verify X in browser" without micro-managing steps
4. **Skill integration** - Skill calls agent automatically

## Agent Interface

**Name:** `browser-navigator`

**Input parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `task` | string | What to do: "Navigate to X and verify Y" | required |
| `url` | string | Starting URL | optional |
| `output` | string[] | What to return: `["summary", "screenshot", "data"]` | `["summary"]` |
| `lifecycle` | string | `"fresh"` \| `"reuse"` \| `"leave_open"` | `"reuse"` |
| `timeout` | number | Max seconds for entire task | `60` |

**Output:**

```json
{
  "success": true,
  "summary": "Page loaded, found 5 products, Save button disabled",
  "screenshot": "/path/to/screenshot.png",
  "data": { "store_name": "ciab-admin", "visibility": "coming_soon" },
  "error": null
}
```

**Example invocation:**

```
Task agent: browser-navigator
prompt: |
  task: "Verify the General settings page loads and shows store name"
  url: "http://localhost:9001/wp-admin/admin.php?page=next-admin&p=/woocommerce/settings/general"
  output: ["summary", "screenshot"]
  lifecycle: "fresh"
```

## Agent Behavior

### Startup Sequence

1. Check for profile lock → auto-recover with `pkill` if detected
2. Connect to existing browser or launch fresh (per `lifecycle` param)
3. Set default timeout from param

### Core Workflow (RULE 0)

```
Navigate → Snapshot → Interact → Verify
         ↑                      │
         └──────────────────────┘
```

Element refs regenerate per snapshot. After navigation, old refs may match DIFFERENT elements. Always take fresh snapshot before any interaction.

### Automatic Recovery (max 3 attempts each)

| Error | Recovery |
|-------|----------|
| Profile lock | `pkill -f "chrome-devtools-mcp/chrome-profile"`, retry |
| Stale ref | Fresh snapshot, get new ref, retry |
| Tool stall | Kill session, restart with explicit timeout |
| Modal blocking | Find close button, dismiss, retry |
| Network timeout | Wait 2s, retry |

### Escalation to Caller (no retry)

- Auth required (401/403)
- Server errors (500/502/503)
- 3 failed recovery attempts
- Task timeout exceeded

### Timeout Enforcement

| Tool | Default Timeout |
|------|-----------------|
| `navigate_page` | 30s |
| `wait_for` | 10s |
| Overall task | `timeout` param (default 60s) |

## Skill Integration

The `browser-interaction` skill becomes a lightweight dispatcher:

```markdown
# Browser Interaction

Browser automation via the `browser-navigator` agent.

## Usage

Always delegate browser tasks to the agent:

Task agent: browser-navigator
prompt: |
  task: "<description of what to do>"
  url: "<starting URL>"
  output: ["summary"]  # or ["summary", "screenshot", "data"]
  lifecycle: "reuse"   # or "fresh", "leave_open"

## When to Use

- Verifying UI changes after code modifications
- Debugging frontend issues
- Taking screenshots for documentation
- Extracting data from rendered pages
- Testing user flows

## Reference

For error patterns, timeout values, and MCP tool mapping,
see the agent's built-in documentation.
```

**Benefits:**
- Skill stays tiny (reduces context load when loaded)
- Single source of truth for browser behavior (agent)
- Consistent behavior whether called from main session or subagent

## File Structure

**Agent file:** `plugins/pirategoat-tools/agents/browser-navigator.md`

```markdown
---
name: browser-navigator
description: Executes browser automation in isolation. Handles navigation,
  verification, screenshots, data extraction. Auto-recovers from profile
  locks, stale refs, timeouts.
tools: [mcp__chrome-devtools__*, Bash]
---

# Browser Navigator Agent

[Full agent prompt with interface, behavior, recovery logic]
```

**Updated skill:** `plugins/pirategoat-tools/skills/browser-interaction/SKILL.md`

Simplified to dispatcher + reference documentation.

## Testing Plan

1. **Profile lock recovery:** Start with orphaned Chrome process, verify auto-recovery
2. **Timeout enforcement:** Navigate to slow/unresponsive page, verify timeout triggers
3. **RULE 0 compliance:** Verify snapshot taken after each navigation
4. **Output modes:** Test summary-only, screenshot, and data extraction
5. **Lifecycle modes:** Test `fresh`, `reuse`, and `leave_open`
6. **Error escalation:** Verify auth errors and server errors are reported, not retried

## Implementation Steps

1. Create `browser-navigator.md` agent file with full prompt
2. Update `browser-interaction` skill to dispatcher format
3. Test all scenarios from testing plan
4. Release as patch version (1.10.2)

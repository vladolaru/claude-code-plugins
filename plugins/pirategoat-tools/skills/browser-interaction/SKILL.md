---
name: browser-interaction
description: Use when automating browser tasks - clicking, filling forms, taking screenshots, debugging UI, or testing web flows. Requires chrome-devtools or playwright MCP.
---

# Browser Interaction

Browser automation via MCP tools (chrome-devtools or playwright).

## Prerequisites

One of these MCP servers must be connected:
- **chrome-devtools** - Connect to Chrome with remote debugging
- **playwright** - Headless/headed browser automation

## Quick Start

```
1. ToolSearch query: "+chrome-devtools list pages snapshot navigate"
2. Navigate: mcp__chrome-devtools__navigate_page(type: "url", url: "...")
3. Snapshot: mcp__chrome-devtools__take_snapshot()
4. Interact using uid from snapshot
```

## MCP Detection

**MCP tools are deferred - load via ToolSearch first:**

```
ToolSearch query: "+chrome-devtools list pages snapshot navigate screenshot"
```

If chrome-devtools unavailable, try playwright:
```
ToolSearch query: "+playwright browser snapshot navigate"
```

## Tool Mapping

| Action | Chrome DevTools | Playwright |
|--------|-----------------|------------|
| List pages | `list_pages` | `browser_tabs` |
| Navigate | `navigate_page` | `browser_navigate` |
| Click | `click` | `browser_click` |
| Fill | `fill` | `browser_type` |
| Screenshot | `take_screenshot` | `browser_take_screenshot` |
| Snapshot | `take_snapshot` | `browser_snapshot` |
| Wait | `wait_for` | `browser_wait_for` |

## RULE 0: Fresh Snapshot After Navigation

After ANY navigation, take a fresh snapshot BEFORE clicking or filling:

```
Navigate → Snapshot → Interact → Verify
         ↑                      │
         └──────────────────────┘
```

**Why:** Element refs (`uid="1_5"`) regenerate per snapshot. Old refs match DIFFERENT elements after navigation.

## Common Operations

**Navigate and inspect:**
```
mcp__chrome-devtools__navigate_page(type: "url", url: "http://localhost:9001/wp-admin/", timeout: 30000)
mcp__chrome-devtools__take_snapshot()
```

**Take screenshot:**
```
mcp__chrome-devtools__take_screenshot(filePath: "/tmp/screenshot.png", fullPage: true)
```

**Click element:**
```
# Get uid from snapshot first
mcp__chrome-devtools__click(uid: "1_42", includeSnapshot: true)
```

**Fill input:**
```
mcp__chrome-devtools__fill(uid: "1_15", value: "test@example.com")
```

## Error Recovery

| Error | Recovery |
|-------|----------|
| "browser is already running" | `pkill -f "chrome-devtools-mcp/chrome-profile"`, retry |
| "Ref not found" / "No node with given id" | Fresh snapshot, get new uid, retry |
| Network timeout | Wait 2s, retry (max 3 attempts) |

## When to Use

- Verifying UI changes after code modifications
- Debugging frontend issues
- Taking screenshots for documentation
- Extracting data from rendered pages
- Testing user flows

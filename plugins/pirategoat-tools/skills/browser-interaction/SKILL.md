---
name: browser-interaction
description: Use when automating browser tasks - clicking, filling forms, taking screenshots, debugging UI, or testing web flows. Requires chrome-devtools or playwright MCP.
---

# Browser Interaction

Browser automation via MCP tools (chrome-devtools or playwright).

## Prerequisites

Load browser MCP tools before any interaction:

```
ToolSearch query: "+chrome-devtools list pages snapshot navigate screenshot"
```

If chrome-devtools unavailable, fall back to playwright:

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

## RULE 0: Fresh Snapshot Before Interaction

Every browser interaction follows this loop:

```
Navigate → Fresh Snapshot → Interact (using uid) → Verify
         ↑                                        │
         └────────────────────────────────────────┘
```

Element refs (`uid="1_5"`) regenerate per snapshot. Old refs match DIFFERENT elements after navigation.

**Navigate:**
```
mcp__chrome-devtools__navigate_page(type: "url", url: "http://localhost:9001/wp-admin/", timeout: 30000)
```

**Snapshot → Interact:**
```
mcp__chrome-devtools__take_snapshot()
mcp__chrome-devtools__click(uid: "1_42", includeSnapshot: true)
mcp__chrome-devtools__fill(uid: "1_15", value: "test@example.com")
```

**Verify (see RULE 1 for screenshot selection):**
```
mcp__chrome-devtools__take_screenshot(uid: "3_272")
```

## RULE 1: Token-Efficient Interaction

Choose the cheapest tool for your goal:

| Goal | Tool | Typical tokens |
|------|------|----------------|
| Interact (click/fill/read) | Snapshot | ~50-500 (simple pages) |
| Visual check (layout/styling) | Screenshot with `uid` | ~300-1,300 |
| Full page context (last resort) | Screenshot (no uid) | ~1,500-1,900 |

Target elements, not full viewport. Use the `uid` parameter on `take_screenshot` to capture only the relevant section. On WP admin pages, target `<main>` to skip the sidebar (~250 nav elements).

**Warning:** On heavy-navigation pages (WP admin, WooCommerce), snapshots can exceed screenshot costs because the a11y tree includes every sidebar/toolbar link. Prefer element-targeted screenshots for visual checks on these pages.

Image token cost = `(width × height) / 750`. Format, compression, and color depth have zero effect.

## Error Recovery

| Error | Recovery |
|-------|----------|
| "browser is already running" | Kill stuck browser (see Reference below) |
| "Ref not found" / "No node with given id" | Fresh snapshot, get new uid, retry |
| Network timeout | Wait 2s, retry (max 3 attempts) |

## Reference

### Chrome DevTools Profile Locations

chrome-devtools-mcp stores browser profiles at:

| Mode | Profile path | pkill pattern |
|------|-------------|---------------|
| Default | `$HOME/.cache/chrome-devtools-mcp/chrome-profile` | `chrome-devtools-mcp/chrome-profile` |
| `--isolated` | OS temp dir, e.g. `/var/folders/.../puppeteer_dev_chrome_profile-XXXXXX` | `puppeteer_dev_chrome_profile` |

The profile persists across runs unless `--isolated` is used. A killed Chrome process leaves a `SingletonLock` file in the profile dir that blocks the next launch.

### Killing a Stuck Browser

When chrome-devtools-mcp reports "browser is already running":

```bash
# 1. Try isolated profile first (temp dir) — kills ALL isolated instances
pkill -f 'puppeteer_dev_chrome_profile'

# 2. If that didn't match, try default persistent profile
pkill -f 'chrome-devtools-mcp/chrome-profile'
rm -f "$HOME/.cache/chrome-devtools-mcp/chrome-profile/SingletonLock"
```

Wait 2 seconds after killing before retrying.

**Note:** With `--isolated`, each session gets a unique temp dir (e.g. `puppeteer_dev_chrome_profile-RGjl4g`). The pkill pattern above kills all isolated instances. There is no reliable way to target a specific one without tracing PIDs through the process tree.

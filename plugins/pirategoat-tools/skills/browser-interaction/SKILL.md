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

```
# WRONG: uid reused after navigation
take_snapshot()            # uid="1_42" → Save button
navigate_page(url="/foo")
click(uid="1_42")          # uid "1_42" may now match an unrelated element

# RIGHT: fresh snapshot after every navigation
navigate_page(url="/foo")
take_snapshot()            # fresh uids
click(uid=<new>)
```

**Navigate:**
```
mcp__chrome-devtools__navigate_page(type: "url", url: "http://localhost:8080/wp-admin/", timeout: 30000)
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

## Capability Patterns

Common capabilities composed from the base tools. Each follows RULE 0 (fresh snapshot before interaction).

| Capability | Reach for when... |
|---|---|
| Viewport / device emulation | Walking breakpoints |
| Theme switching | Verifying every theme in scope |
| Network throttling | Exercising loading states |
| Request blocking | Exercising error states |
| Accessibility scan | Auditing per surface against the WCAG bar |
| Keyboard navigation | Verifying every affordance reachable and operable by keyboard |
| Scroll | Verifying behavior at scrollY > 0 (sticky, chrome falling off, infinite-scroll triggers) |
| Focus tracking | Verifying focus return after modal close, dropdown collapse, async completion |
| Multi-perspective sign-in | Evaluating role-distinct or visibility-distinct experiences |
| Console + network log inspection | Surfacing findings the visible UI doesn't show |

### Viewport / device emulation

| Tool | Call |
|---|---|
| Chrome DevTools | `emulate({ width, height, deviceScaleFactor, isMobile, hasTouch })` for full emulation; `resize_page({ width, height })` for plain resize |
| Playwright | `browser_resize({ width, height })` |

Emulation also sets user-agent and touch-event behavior; resize only changes dimensions. Use emulation for mobile testing where touch/UA matter; resize for desktop sizing. Verify with a fresh snapshot.

Common breakpoints: 375 / 768 / 1280 / 1920 (mobile / tablet / desktop / wide).

### Theme switching

Three patterns, in order of preference:

1. **UI toggle** — click the product's theme button. Most authentic; tests the actual user path.
2. **Class injection** — `evaluate_script` / `browser_evaluate` toggling a class on `<html>` or `<body>`:
   ```js
   document.documentElement.classList.toggle('dark')
   ```
3. **prefers-color-scheme emulation** (Chrome DevTools only): `emulate({ prefersColorScheme: 'dark' })`.

Pick the pattern that matches how the product implements theming. Verify the switch took effect by querying `getComputedStyle(document.body).backgroundColor` (or another theme-keyed property) via `evaluate_script` / `browser_evaluate`, or by a fresh snapshot showing the changed theme.

### Network throttling

Three approaches, in order of fidelity:

1. **Chrome DevTools `emulate` with network conditions** (if your build exposes them) — check parameter schema; Slow-3G ≈ 400 ms latency, 400 kbps down/up.
2. **CDP via `evaluate_script`** — `Network.emulateNetworkConditions` through the debugger (requires chrome.debugger permission in the inspected context).
3. **Fetch wrapper with artificial delay** (universal fallback, works under either MCP):
   ```js
   (() => {
     const DELAY_MS = 2000;
     const orig = window.fetch;
     window.fetch = (...args) => new Promise(r =>
       setTimeout(() => orig(...args).then(r), DELAY_MS));
   })()
   ```
   Run via `evaluate_script` / `browser_evaluate`. Less authentic (latency only, no bandwidth simulation) but reliable.

### Request blocking

To force error states for a specific endpoint, install a fetch override in the page context:

```js
(() => {
  const orig = window.fetch;
  window.fetch = (url, opts) =>
    url.toString().includes('TARGET_URL')
      ? Promise.resolve(new Response('{"error":"forced"}', { status: 500 }))
      : orig(url, opts);
})()
```

Run via `evaluate_script` (Chrome DevTools) or `browser_evaluate` (Playwright). For XHR-based requests, override `XMLHttpRequest.prototype.open` similarly. Replace `TARGET_URL` with the substring identifying the endpoint to block.

### Accessibility scan

`take_snapshot` / `browser_snapshot` returns the a11y tree as part of the snapshot — basic audit surface. For deeper audits:

- **Lighthouse** (Chrome DevTools): `lighthouse_audit` includes accessibility scoring.
- **axe-core** (either tool): inject the script via `evaluate_script` / `browser_evaluate` and read `axe.run()`'s violations report.

When flagging, cite the specific WCAG success criterion (e.g., `1.4.3 Contrast (Minimum)`, `2.1.1 Keyboard`).

### Keyboard navigation

| Action | Chrome DevTools | Playwright |
|---|---|---|
| Single key | `press_key({ key: 'Tab' })` | `browser_press_key({ key: 'Tab' })` |
| Modifier + key | Pass `shift` / `alt` / `ctrl` / `meta` per the tool's schema | Same |

Common sequences (extend the principle: each affordance type implies a key set the user expects to work — match to the widget's ARIA role when in doubt):
- **Lists / menus**: Arrow keys + Enter + Escape.
- **Forms**: Tab / Shift+Tab + Enter.
- **Modals**: Escape closes; Tab cycles within (focus trap should hold).
- **Custom widgets (tablist, combobox, tree, slider, etc.)**: Per the WAI-ARIA Authoring Practices for that role.

After each keypress, fresh snapshot or `evaluate_script` querying `document.activeElement` to verify focus moved as expected.

### Scroll

Scroll programmatically:

```js
window.scrollTo({ top: 0, behavior: 'instant' })           // top
window.scrollTo({ top: document.body.scrollHeight })       // bottom
document.querySelector(selector).scrollIntoView()           // specific element
```

After each scroll, fresh snapshot or element-targeted screenshot to capture state at the new position. Use this to verify sticky elements stay stuck, headers don't disappear, infinite-scroll triggers fire, and chrome doesn't fall off the viewport at scrollY > 0.

### Focus tracking

Query the currently-focused element:

```js
() => `${document.activeElement?.tagName}#${document.activeElement?.id}.${[...document.activeElement?.classList || []].join('.')}`
```

Run via `evaluate_script` / `browser_evaluate`. Use after dismissals (modal close, dropdown collapse, async completion) to verify focus returned somewhere sensible. Focus left on `<body>` after a dismissal is typically a regression.

### Multi-perspective sign-in

Each MCP instance holds its own session cookies. To switch perspectives:

1. Sign out via UI, or clear cookies via `evaluate_script`:
   ```js
   document.cookie.split(';').forEach(c => {
     document.cookie = c.replace(/^ +/, '').replace(/=.*/, '=;expires=' + new Date(0).toUTCString() + ';path=/');
   })
   ```
2. Navigate to the sign-in surface.
3. Fill + submit credentials for the next perspective.
4. Verify the signed-in state changed (different identity in chrome, different sidebar entries, different visibility on shared resources).

For dev-mode magic-link flows that return the verification URL inline, check `list_network_requests` / `browser_network_requests` if the auto-redirect doesn't trigger; manually navigate to the verification URL.

### Console + network log inspection

| Concern | Chrome DevTools | Playwright |
|---|---|---|
| Console messages | `list_console_messages` → `get_console_message(idx)` for detail | `browser_console_messages` |
| Network requests | `list_network_requests` → `get_network_request(idx)` for detail | `browser_network_requests` → `browser_network_request` |

After any meaningful interaction, query both. Console errors and 4xx/5xx network responses are findings the visible UI may not surface. Read every entry — not just the one prompted by your question.

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

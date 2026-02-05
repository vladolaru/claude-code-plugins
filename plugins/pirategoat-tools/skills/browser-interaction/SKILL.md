---
name: browser-interaction
description: Use when automating browser tasks - clicking, filling forms, taking screenshots, debugging UI, or testing web flows. Requires a browser automation MCP server (chrome-devtools, playwright, or puppeteer).
---

# Browser Interaction

Browser automation for debugging, verification, testing, and exploration.

**Complete the task requested. Do not add extra checks beyond what is asked.**

## RULE 0 (CRITICAL): Fresh Snapshot Before Every Interaction

After ANY navigation, call `take_snapshot` or `browser_snapshot` BEFORE clicking or filling.

```
Navigate → Snapshot → Interact → Verify
         ↑                      │
         └──────────────────────┘ (repeat)
```

**Why:** Element refs (`e6`, `uid="3_5"`) regenerate per snapshot. After navigation, old refs may match DIFFERENT elements, causing silent wrong-element clicks.

<example type="CORRECT">
navigate(url_A) → snapshot() → click(ref_from_A)
navigate(url_B) → snapshot() → click(ref_from_B)  # Fresh ref!
</example>

<example type="INCORRECT">
navigate(url_A) → snapshot() → click(ref_from_A)
navigate(url_B) → click(ref_from_A)  # WRONG: stale ref, silent fail!
</example>

---

## Pre-flight: Detect Available MCP

Check which browser MCP is available (in order of preference):

| MCP | Detection Tool | Install Command |
|-----|----------------|-----------------|
| Chrome DevTools | `mcp__chrome-devtools__list_pages` | `claude mcp add chrome-devtools npx chrome-devtools-mcp@latest` |
| Playwright | `mcp__playwright__browser_snapshot` | `claude mcp add playwright npx @playwright/mcp@latest` |
| Puppeteer | `mcp__puppeteer__puppeteer_navigate` | `claude mcp add puppeteer npx puppeteer-mcp@latest` |

Use the first available. If none available, suggest install command and restart Claude Code.

---

## Profile Lock Errors (Chrome DevTools)

**Error:** `"The browser is already running for .../chrome-profile"`

This blocks ALL MCP operations. Resolve before proceeding:

```bash
# Find and kill orphaned Chrome processes using MCP profile
pkill -f "chrome-devtools-mcp/chrome-profile"
```

Then retry your navigation. If error persists, the browser may be in use by another Claude session.

**For parallel sessions**, use `--isolated` flag when starting Chrome DevTools MCP to avoid profile conflicts.

---

## Timeouts (CRITICAL)

**Always set timeouts** to prevent indefinite stalls:

| Tool | Timeout Parameter | Default | Recommendation |
|------|-------------------|---------|----------------|
| `navigate_page` | `timeout` (ms) | varies | 30000 (30s) |
| `wait_for` | `timeout` (ms) | varies | 10000 (10s) |
| `click` | implicit | none | N/A |

**Navigation with timeout:**
```
navigate_page(url, timeout=30000)
```

**Wait with timeout:**
```
wait_for(text="Success", timeout=10000)
```

If a tool stalls without timeout, you lose the entire session. Always specify explicit timeouts.

---

## Tool Mapping

| Action | Chrome DevTools | Playwright | Puppeteer |
|--------|-----------------|------------|-----------|
| Navigate | `navigate_page` | `browser_navigate` | `puppeteer_navigate` |
| Click | `click` | `browser_click` | `puppeteer_click` |
| Fill input | `fill` | `browser_type` | `puppeteer_fill` |
| Screenshot | `take_screenshot` | `browser_take_screenshot` | `puppeteer_screenshot` |
| DOM snapshot | `take_snapshot` | `browser_snapshot` | N/A (use screenshot) |
| Console logs | `list_console_messages` | `browser_console_messages` | N/A |
| Network | `list_network_requests` | `browser_network_requests` | N/A |
| Wait | `wait_for` | `browser_wait_for` | N/A |
| Evaluate JS | `evaluate_script` | `browser_evaluate` | `puppeteer_evaluate` |

---

## Common Error Patterns

**Profile lock errors** ("browser is already running for"):
→ See **Profile Lock Errors** section above — kill orphaned processes

**Connection errors** (browser/page closed, "No browser connected", "Target closed"):
→ Reopen browser manually or call navigate to restart session

**Stale ref errors** ("Ref not found", "No node with given id", element not found after navigation):
→ Take fresh snapshot, get new ref — this is RULE 0

**Tool stalls** (no response, hanging indefinitely):
→ You didn't set a timeout. Kill session, restart with explicit timeouts.

**Network errors** (`ERR_CONNECTION_REFUSED`, `ERR_NAME_NOT_RESOLVED`, timeout):
→ Verify server running, check URL/port, try http:// if SSL issues

**HTTP errors:**
- 401/403: Auth required or blocked — ask user
- 404: Wrong URL — verify with user
- 500/502/503: Server error — ask user to fix server

---

## Error Recovery

### Handle Autonomously (max 3 attempts)

| Category | Recovery Action |
|----------|-----------------|
| **Profile lock** | `pkill -f "chrome-devtools-mcp/chrome-profile"`, retry |
| **Stale ref** | Fresh snapshot, get new ref, retry |
| **Session redirect** | Re-authenticate, return to previous URL |
| **Modal blocking** | Find close button, dismiss, retry |
| **Network timeout** | Wait 2-3 seconds, retry |
| **Tool stall** | Kill session (see Closing Browser), restart with timeouts |

### Ask User First

- Credentials needed or wrong
- Destructive actions (delete, reset, clear)
- After 3 failed recovery attempts
- Server errors (500/502/503)
- 404 errors suggesting wrong URL

**When escalating, provide:**
1. What's broken (specific error message)
2. Evidence (console errors, network failures, DOM state)
3. What you tried (list recovery attempts)
4. Why you're blocked (what user action is needed)

---

## Debugging

**Gather evidence first:**
- Console: `list_console_messages` / `browser_console_messages`
- DOM: `take_snapshot` / `browser_snapshot`
- Network: `list_network_requests` / `browser_network_requests`

**Debug loop:** IDENTIFY error → LOCATE source → FIX → REFRESH → VERIFY

---

## Closing the Browser

MCP tools may not fully close browser sessions. Track the browser PID to close cleanly:

**Before first navigation (generate unique session ID):**
```bash
MCP_SID="$$_$RANDOM" && pgrep -f "Chromium|Google Chrome" > /tmp/.mcp_pids_before_$MCP_SID && echo $MCP_SID
```
Save the echoed session ID for later steps.

**After first navigation (captures the new browser PID):**
```bash
pgrep -f "Chromium|Google Chrome" | grep -v -F -f /tmp/.mcp_pids_before_<SID> > /tmp/.mcp_browser_pid_<SID>
```
Replace `<SID>` with the session ID from step 1.

**To close the browser:**
```bash
xargs kill < /tmp/.mcp_browser_pid_<SID> 2>/dev/null; rm -f /tmp/.mcp_pids_before_<SID> /tmp/.mcp_browser_pid_<SID>
```

This only kills the browser instance spawned by MCP, not other Chrome windows.

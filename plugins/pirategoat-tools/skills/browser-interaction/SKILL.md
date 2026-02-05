---
name: browser-interaction
description: Use when automating browser tasks - clicking, filling forms, taking screenshots, debugging UI, or testing web flows. Dispatches to browser-navigator agent for isolated execution.
---

# Browser Interaction

Browser automation via the `browser-navigator` agent. All tasks run in isolation to prevent context pollution and ensure reliable error recovery.

## Usage

**Always delegate browser tasks to the agent:**

```
Task agent: browser-navigator
prompt: |
  task: "<what to do>"
  url: "<starting URL>"
  output: ["summary"]
  lifecycle: "reuse"
```

## Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `task` | string | What to do (required) |
| `url` | string | Starting URL |
| `output` | `["summary"]`, `["summary", "screenshot"]`, `["summary", "data"]` | What to return |
| `lifecycle` | `"fresh"`, `"reuse"`, `"leave_open"` | Browser session handling |
| `timeout` | number (seconds) | Max time for task (default: 60) |

## When to Use

- Verifying UI changes after code modifications
- Debugging frontend issues
- Taking screenshots for documentation
- Extracting data from rendered pages
- Testing user flows

## Examples

**Verify a page loads:**
```
Task agent: browser-navigator
prompt: |
  task: "Verify the settings page loads and shows the save button"
  url: "http://localhost:9001/wp-admin/admin.php?page=next-admin"
  output: ["summary"]
```

**Take a screenshot:**
```
Task agent: browser-navigator
prompt: |
  task: "Screenshot the Products settings page"
  url: "http://localhost:9001/wp-admin/admin.php?page=next-admin&p=/woocommerce/settings/products"
  output: ["summary", "screenshot"]
  lifecycle: "fresh"
```

**Extract data:**
```
Task agent: browser-navigator
prompt: |
  task: "Get the store name and visibility setting"
  url: "http://localhost:9001/wp-admin/admin.php?page=next-admin&p=/woocommerce/settings/general"
  output: ["summary", "data"]
```

## Agent Capabilities

The `browser-navigator` agent handles automatically:

- **Profile lock recovery** - Clears orphaned Chrome processes
- **Timeout enforcement** - 30s navigation, 10s waits, configurable overall
- **RULE 0 compliance** - Fresh snapshot after every navigation
- **Error recovery** - Stale refs, modals, network timeouts (max 3 retries)
- **Clean output** - Structured results with summary, screenshots, extracted data

See the agent documentation for full details on error handling and recovery behavior.

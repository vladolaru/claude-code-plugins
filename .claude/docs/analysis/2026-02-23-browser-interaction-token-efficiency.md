# Browser Interaction Token Efficiency Analysis

**Date:** 2026-02-23
**Context:** Investigated whether grayscale screenshots, format changes, or alternative approaches can reduce token consumption during browser automation with Claude Code via chrome-devtools MCP.

## Test Setup

- **Page:** WooCommerce Settings > Payments (`wc-settings&tab=checkout`) on a CIAB local installation
- **Viewport:** 1200x1227 px at 1x DPR
- **MCP server:** chrome-devtools
- **Page characteristics:** WP admin with full sidebar navigation, toolbar, two notice banners, and a Payment providers section with 3 payment methods + expandable "More options"

## Core Finding: How Claude Tokenizes Images

Claude calculates image tokens purely from pixel dimensions:

```
tokens = (width × height) / 750
```

**Format, file size, compression quality, and color depth have zero effect on token count.** A grayscale JPEG at 50% quality costs exactly the same tokens as a full-color PNG of the same pixel dimensions. Grayscale only reduces file transfer size (smaller payload over the wire), not LLM processing cost.

If the long edge exceeds 1568px or total exceeds ~1.15 megapixels, Claude auto-downscales before tokenizing. This adds latency (increased time-to-first-token) with no benefit — pre-resize to avoid it.

**Contrast with OpenAI:** GPT-4o offers a `"detail": "low"` mode at a flat 85 tokens regardless of image size. Claude has no equivalent low-detail mode.

## Measured Results

### Screenshot Variants (same 1040x917 main content area)

| Variant | Pixels | Tokens | Visual Quality | File Size |
|---------|--------|--------|---------------|-----------|
| PNG (default) | 1040x917 | ~1,272 | Lossless | Largest |
| JPEG quality 50 | 1040x917 | ~1,272 | Slight softening | ~5-10x smaller |
| Grayscale JPEG q50 | 1040x917 | ~1,272 | No color info, softer | Smallest |

**Conclusion:** All three cost identical tokens. Format choice only affects file transfer speed and visual fidelity, not LLM cost.

### Approach Comparison (full page)

| Approach | Tokens | Can Interact? | Visual Info |
|----------|--------|---------------|-------------|
| Full a11y snapshot | ~3,000-3,750 | Yes (346 UIDs) | None (text only) |
| Full viewport screenshot (1200x1227 PNG) | ~1,906 | No | Full |
| Element screenshot — main content (1040x917) | ~1,272 | No | Main content only |
| Element screenshot — hypothetical small component (500x400) | ~267 | No | Component only |

### The WP Admin Sidebar Problem

The a11y tree snapshot included **346 UIDs**. Breakdown:

| Page Region | UID Range | Element Count | % of Snapshot |
|-------------|-----------|---------------|---------------|
| Sidebar navigation | 3_1 – 3_258 | ~258 | ~75% |
| Toolbar | 3_259 – 3_270 | ~12 | ~3% |
| **Main content (what we care about)** | **3_271 – 3_342** | **~72** | **~21%** |
| Footer/live regions | 3_343 – 3_346 | ~4 | ~1% |

The sidebar alone (Dashboard, Posts, Media, Pages, Comments, MailPoet, WooCommerce with all submenus, Products, Bookings, Payments, Analytics, Marketing, Appearance, Plugins, Users, Tools, Settings, Jetpack Debug, Gutenberg, CIAB Admin, WCPay Dev) contributed ~75% of the snapshot's content. This is consistent across all WP admin pages.

**Key insight:** On WP admin pages, a full a11y snapshot is ~2-3x MORE expensive than a targeted screenshot of the main content area.

## Grayscale Workaround (Works but Pointless for Tokens)

Neither chrome-devtools nor Playwright MCP expose grayscale screenshot parameters natively. However, a CSS workaround works:

```javascript
// Before screenshot
evaluate_script(() => { document.documentElement.style.filter = 'grayscale(1)'; })
// Take screenshot
take_screenshot(uid: "...", format: "jpeg", quality: 50)
// After screenshot
evaluate_script(() => { document.documentElement.style.filter = ''; })
```

The CDP `Emulation.setEmulatedVisionDeficiency('achromatopsia')` would be cleaner (rendering-pipeline-level grayscale), but the chrome-devtools MCP `emulate` tool does not expose vision deficiency emulation — only `colorScheme`, `viewport`, `geolocation`, `networkConditions`, `userAgent`, and `cpuThrottlingRate`.

**Verdict:** Technically achievable but provides zero token savings. Only useful if you genuinely need to test grayscale appearance.

## Optimal Strategy

### For interaction tasks (clicking, filling, reading text)

Use `take_snapshot()` — it provides UIDs needed for `click`, `fill`, `hover`. No screenshot needed.

### For visual verification (layout, styling, icons)

Use `take_screenshot(uid: "<main-content-uid>")` — target the main content container to skip sidebar/toolbar noise. Saves ~33% tokens vs full viewport on WP admin pages.

### For multi-step automation sessions

1. **First interaction:** `take_snapshot()` to get UIDs (~3,500 tokens on WP admin, but necessary)
2. **Subsequent interactions:** Reuse UIDs from the same page; only re-snapshot after navigation (per RULE 0)
3. **Visual checks:** Element-targeted screenshots only when visual verification is genuinely needed
4. **Never:** `take_screenshot(fullPage: true)` unless documenting the entire page layout

### Token budget per page interaction

| Step | Tool | Tokens |
|------|------|--------|
| Initial snapshot (WP admin) | `take_snapshot()` | ~3,000-3,750 |
| Initial snapshot (simple page) | `take_snapshot()` | ~50-500 |
| Visual check (element) | `take_screenshot(uid: "...")` | ~300-1,300 |
| Visual check (viewport) | `take_screenshot()` | ~1,500-1,900 |
| Visual check (full page) | `take_screenshot(fullPage: true)` | ~2,000-5,000+ |

## Approaches NOT Available Today (Future Possibilities)

| Approach | Token Impact | Status |
|----------|-------------|--------|
| Claude "low detail" vision mode (like OpenAI's) | Would cap at ~85 tokens | Not available in Claude API |
| CDP `setEmulatedVisionDeficiency` in chrome-devtools MCP | Zero (same pixels) | MCP doesn't expose it |
| Filtered/partial a11y snapshots (skip sidebar) | Could reduce snapshot to ~500-800 tokens | Not supported by MCP |
| Vercel agent-browser (compact refs) | 93% context savings vs Playwright MCP | Different tool, not integrated |
| Prompt caching for repeated images | 90% cost on cached reads | Available but requires API-level integration |

## Action Taken

Updated `browser-interaction` skill in pirategoat-tools with:
- **RULE 1: Token-Efficient Interaction** — decision flowchart, token formula, trade-offs table, WP admin warning
- Updated Common Operations to default to element-targeted screenshots instead of `fullPage: true`
- Word count: 510 → 790 words (within budget for non-frequently-loaded skills)
